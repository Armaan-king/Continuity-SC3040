"""Application tables over the same connection pool the checkpointer uses.

## Why this is not an ORM

`langgraph-checkpoint-postgres` already brings psycopg3 and `psycopg_pool`, so a second
driver and a mapping layer would buy nothing: the schema is four tables of flat columns
and the queries are all single-statement. Sharing one pool also means one place where
connection limits, timeouts and shutdown are decided.

## Why the user-scoped lookups take a `user_id` rather than filtering afterwards

`project_for_user` and `thread_for_user` are the authorisation boundary for `/resume`,
`/export` and everything under `/projects`. Fetching by id and comparing the owner in
Python is the same logic with one more place to forget it, so ownership is a `WHERE`
clause and there is no unscoped read to reach for by accident.

A miss returns `None` and the route answers 404 rather than 403 — a 403 confirms that a
thread exists, which is a fact the caller has no business learning.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool

from .findings import Finding
from ..parts.dossier import DOSSIER_FIELDS

SCHEMA = Path(__file__).with_name("schema.sql")

SESSION_IDLE_SECONDS = 60 * 30
"""How long a session survives with no requests at all.

Sliding, not absolute: every authenticated request pushes `expires_at` out again, so the
window only runs down while nobody is using the app. A design run — including one paused
on an escalation question, because answering it is a request — keeps its own session alive,
which is the property that makes an idle timeout safe to have during a live demo.

Thirty minutes is the shortest window that cannot plausibly expire mid-sentence. Two
minutes was considered and rejected for exactly that: a run that stops to ask a question
while somebody talks over it would 401 its own answer.
"""

SESSION_TTL_SECONDS = 60 * 60 * 24 * 14
"""The absolute ceiling, measured from when the session was minted.

An idle timeout alone can be slid forever, so a stolen cookie that is *used* never expires.
This is the backstop, and it is why `user_for_token` clamps with `LEAST` rather than simply
adding the idle window each time.
"""


class EmailTaken(Exception):
    """Registration hit the unique constraint on `users.email`."""


@dataclass(frozen=True)
class User:
    id: str
    email: str
    password_hash: str
    onboarded_at: datetime | None


@dataclass(frozen=True)
class Project:
    id: str
    user_id: str
    name: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Thread:
    id: str
    project_id: str
    user_id: str
    prompt: str
    status: str
    last_seq: int
    bom: list[dict[str, Any]] | None
    summary: dict[str, Any] | None


def new_id() -> str:
    """Same shape as the thread ids already in the wire format."""
    return uuid.uuid4().hex[:12]


def _fold(email: str) -> str:
    """One spelling per account. Folded before every read and every write."""
    return email.strip().lower()


WALKTHROUGH_PROJECT_NAME = "Welcome to Continuity"
SCRATCH_PROJECT_NAME = "Scratch designs"


def _derived_id(user_id: str, purpose: str) -> str:
    """A stable id for a row an account may only have one of.

    Same shape as `new_id()`, but reproducible — which is what lets two concurrent
    requests insert the same row instead of two different ones.
    """
    return hashlib.sha256(f"{purpose}:{user_id}".encode()).hexdigest()[:12]


def _hash_token(token: str) -> str:
    """What is stored. The cookie value itself never reaches the database."""
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def setup(self) -> None:
        """Apply the schema. Idempotent, like `checkpointer.setup()`."""
        async with self.pool.connection() as conn:
            await conn.execute(SCHEMA.read_text())

    # ── users ────────────────────────────────────────────────────────────────

    async def create_user(self, email: str, password_hash: str) -> User:
        user_id = new_id()
        try:
            async with self.pool.connection() as conn:
                await conn.execute(
                    "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
                    (user_id, _fold(email), password_hash),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise EmailTaken(email) from exc

        return User(id=user_id, email=_fold(email), password_hash=password_hash, onboarded_at=None)

    async def user_by_email(self, email: str) -> User | None:
        return await self._one_user("WHERE email = %s", (_fold(email),))

    async def user_by_id(self, user_id: str) -> User | None:
        return await self._one_user("WHERE id = %s", (user_id,))

    async def update_password_hash(self, user_id: str, password_hash: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id)
            )

    async def mark_onboarded(self, user_id: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE users SET onboarded_at = now() WHERE id = %s AND onboarded_at IS NULL",
                (user_id,),
            )

    async def _one_user(self, where: str, params: tuple[Any, ...]) -> User | None:
        async with self.pool.connection() as conn:
            cursor = await conn.cursor(row_factory=dict_row).execute(
                f"SELECT id, email, password_hash, onboarded_at FROM users {where}", params
            )
            row = await cursor.fetchone()
        return None if row is None else User(**row)

    # ── sessions ─────────────────────────────────────────────────────────────

    async def create_session(
        self, user_id: str, ttl_seconds: int = SESSION_IDLE_SECONDS
    ) -> str:
        """Mint a session and return the raw token. Only its hash is kept.

        It starts with one idle window in front of it, not the absolute ceiling — the
        ceiling is a limit on how far the window may later be slid, not a grant.
        """
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                (_hash_token(token), user_id, expires),
            )
        return token

    async def user_for_token(self, token: str) -> User | None:
        """The whole auth check *and* the sliding renewal, in one statement.

        Validating and renewing separately would be two round trips and a race — a request
        arriving in the last moments of a window could read it live and renew it dead. As
        one `UPDATE … RETURNING`, a session is renewed exactly when it is found valid.

        `LEAST` is what keeps the absolute ceiling meaningful: without it, a session that is
        used every twenty minutes would be renewed for ever.

        **Every authenticated request writes a row.** That is a deliberate trade at this
        scale — the alternative, only renewing once a window is half spent, needs the read
        and the write in separate branches and reintroduces the race this shape removes.
        """
        idle = timedelta(seconds=SESSION_IDLE_SECONDS)
        absolute = timedelta(seconds=SESSION_TTL_SECONDS)
        async with self.pool.connection() as conn:
            cursor = await conn.cursor(row_factory=dict_row).execute(
                """
                WITH slid AS (
                    UPDATE sessions
                       SET expires_at = LEAST(now() + %(idle)s, created_at + %(absolute)s)
                     WHERE token_hash = %(token_hash)s
                       AND expires_at > now()
                       AND created_at + %(absolute)s > now()
                 RETURNING user_id
                )
                SELECT u.id, u.email, u.password_hash, u.onboarded_at
                  FROM slid
                  JOIN users u ON u.id = slid.user_id
                """,
                {"idle": idle, "absolute": absolute, "token_hash": _hash_token(token)},
            )
            row = await cursor.fetchone()
        return None if row is None else User(**row)

    async def delete_session(self, token: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "DELETE FROM sessions WHERE token_hash = %s", (_hash_token(token),)
            )

    async def delete_expired_sessions(self) -> int:
        async with self.pool.connection() as conn:
            cursor = await conn.execute("DELETE FROM sessions WHERE expires_at <= now()")
            return cursor.rowcount

    # ── projects ─────────────────────────────────────────────────────────────

    async def create_project(
        self, user_id: str, name: str, *, is_walkthrough: bool = False
    ) -> Project:
        async with self.pool.connection() as conn:
            cursor = await conn.cursor(row_factory=dict_row).execute(
                """
                INSERT INTO projects (id, user_id, name, is_walkthrough)
                VALUES (%s, %s, %s, %s)
                RETURNING id, user_id, name, created_at, updated_at
                """,
                (new_id(), user_id, name, is_walkthrough),
            )
            row = await cursor.fetchone()
        return Project(**row)

    async def projects_for_user(self, user_id: str) -> list[Project]:
        async with self.pool.connection() as conn:
            cursor = await conn.cursor(row_factory=dict_row).execute(
                """
                SELECT id, user_id, name, created_at, updated_at
                  FROM projects WHERE user_id = %s ORDER BY updated_at DESC
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [Project(**row) for row in rows]

    async def project_for_user(self, project_id: str, user_id: str) -> Project | None:
        async with self.pool.connection() as conn:
            cursor = await conn.cursor(row_factory=dict_row).execute(
                """
                SELECT id, user_id, name, created_at, updated_at
                  FROM projects WHERE id = %s AND user_id = %s
                """,
                (project_id, user_id),
            )
            row = await cursor.fetchone()
        return None if row is None else Project(**row)

    async def ensure_scratch_project(self, user_id: str) -> str:
        """The account's visible, reusable home for runs started without a project.

        The id is derived from the account, so React's development double requests use
        the same insert and `ON CONFLICT DO NOTHING` keeps them to one project.
        """
        project_id = _derived_id(user_id, "scratch-project")

        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO projects (id, user_id, name)
                VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING
                """,
                (project_id, user_id, SCRATCH_PROJECT_NAME),
            )

        return project_id

    async def ensure_walkthrough(self, user_id: str, prompt: str) -> str:
        """The account's one walkthrough thread, creating it if it is not there yet.

        Returns the thread id. **Safe to call concurrently**, which it has to be: React
        re-runs effects in development, so two requests arrive within a millisecond of
        each other and a find-then-create loses the race with itself — every new account
        ended up with two "Welcome to Continuity" projects, the first abandoned mid-stream.

        Both ids are derived from the user id rather than random, so the two callers
        compute the *same* rows and `ON CONFLICT DO NOTHING` settles it without a lock.
        """
        project_id = _derived_id(user_id, "walkthrough-project")
        thread_id = _derived_id(user_id, "walkthrough-thread")

        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO projects (id, user_id, name, is_walkthrough)
                VALUES (%s, %s, %s, true) ON CONFLICT (id) DO NOTHING
                """,
                (project_id, user_id, WALKTHROUGH_PROJECT_NAME),
            )
            await conn.execute(
                """
                INSERT INTO threads (id, project_id, user_id, prompt)
                VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING
                """,
                (thread_id, project_id, user_id, prompt),
            )

        return thread_id

    async def walkthrough_thread_for_user(self, user_id: str) -> Thread | None:
        """The walkthrough this account has already been given, if any.

        `/design/demo` is reached more than once — React re-runs effects in development,
        and a refresh mid-tour would do it too — so it looks here first and replays into
        the thread it already made rather than creating another.
        """
        async with self.pool.connection() as conn:
            cursor = await conn.cursor(row_factory=dict_row).execute(
                """
                SELECT t.id, t.project_id, t.user_id, t.prompt, t.status, t.last_seq,
                       t.bom, t.summary
                  FROM threads t
                  JOIN projects p ON p.id = t.project_id
                 WHERE t.user_id = %s AND p.is_walkthrough
                 ORDER BY t.created_at
                 LIMIT 1
                """,
                (user_id,),
            )
            row = await cursor.fetchone()
        return None if row is None else Thread(**row)

    async def rename_project(self, project_id: str, user_id: str, name: str) -> bool:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "UPDATE projects SET name = %s, updated_at = now() WHERE id = %s AND user_id = %s",
                (name, project_id, user_id),
            )
            return cursor.rowcount > 0

    async def delete_project(self, project_id: str, user_id: str) -> bool:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM projects WHERE id = %s AND user_id = %s", (project_id, user_id)
            )
            return cursor.rowcount > 0

    # ── threads ──────────────────────────────────────────────────────────────

    async def create_thread(
        self, thread_id: str, project_id: str, user_id: str, prompt: str
    ) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO threads (id, project_id, user_id, prompt)
                VALUES (%s, %s, %s, %s)
                """,
                (thread_id, project_id, user_id, prompt),
            )
            await conn.execute(
                "UPDATE projects SET updated_at = now() WHERE id = %s", (project_id,)
            )

    async def thread_for_user(self, thread_id: str, user_id: str) -> Thread | None:
        async with self.pool.connection() as conn:
            cursor = await conn.cursor(row_factory=dict_row).execute(
                """
                SELECT id, project_id, user_id, prompt, status, last_seq, bom, summary
                  FROM threads WHERE id = %s AND user_id = %s
                """,
                (thread_id, user_id),
            )
            row = await cursor.fetchone()
        return None if row is None else Thread(**row)

    async def threads_for_project(self, project_id: str, user_id: str) -> list[Thread]:
        """Most relevant run first — which is not always the newest one.

        React's development double-invoke starts two runs half a millisecond apart. The
        second is cancelled immediately and ends `abandoned` at `last_seq = -1`, having
        emitted nothing, while the first goes on to do the actual work. Ordering by
        `created_at` alone therefore hands the caller the empty twin: a project sitting on
        an unanswered question opened to *"this run has no board to restore"*, because the
        run being restored was the phantom rather than the one holding the board.

        So: a run in flight first, then any run that actually emitted a frame, then newest.
        `last_seq = -1` means nothing was ever sent, which is the honest definition of a
        thread with no run behind it.
        """
        async with self.pool.connection() as conn:
            cursor = await conn.cursor(row_factory=dict_row).execute(
                """
                SELECT id, project_id, user_id, prompt, status, last_seq, bom, summary
                  FROM threads WHERE project_id = %s AND user_id = %s
                 ORDER BY (status = 'running') DESC, (last_seq >= 0) DESC, created_at DESC
                """,
                (project_id, user_id),
            )
            rows = await cursor.fetchall()
        return [Thread(**row) for row in rows]

    async def save_progress(self, thread_id: str, last_seq: int, status: str) -> None:
        """Written when a stream ends — which is exactly when a resume becomes possible."""
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                UPDATE threads SET last_seq = %s, status = %s, updated_at = now()
                 WHERE id = %s
                """,
                (last_seq, status, thread_id),
            )

    async def save_summary(self, thread_id: str, summary: dict[str, Any]) -> None:
        """What the engine said about the finished board, stored as it was emitted.

        Not recomputed here and not recomputed by whoever reads it. `conflicts_resolved`
        counts repairs the graph actually applied — a waived fault is deliberately not in
        it — and a dashboard that re-derived any of this from the BOM would be inventing
        a second opinion the engine never gave.
        """
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE threads SET summary = %s, updated_at = now() WHERE id = %s",
                (Json(summary), thread_id),
            )

    async def save_bom(self, thread_id: str, rows: list[dict[str, Any]]) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE threads SET bom = %s, updated_at = now() WHERE id = %s",
                (Json(rows), thread_id),
            )

    async def save_run_events(self, thread_id: str, events: Sequence[dict[str, Any]]) -> None:
        """Append compact stream frames once; duplicate sequence numbers are harmless."""
        rows = [
            (thread_id, event["seq"], Json(event))
            for event in events
            if event.get("type") != "bom" and isinstance(event.get("seq"), int)
        ]
        if not rows:
            return
        async with self.pool.connection() as conn:
            cursor = conn.cursor()
            await cursor.executemany(
                """
                INSERT INTO run_events (thread_id, seq, event) VALUES (%s, %s, %s)
                ON CONFLICT (thread_id, seq) DO NOTHING
                """,
                rows,
            )

    async def run_events(self, thread_id: str) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            cursor = await conn.cursor(row_factory=dict_row).execute(
                "SELECT event FROM run_events WHERE thread_id = %s ORDER BY seq", (thread_id,)
            )
            rows = await cursor.fetchall()
        return [row["event"] for row in rows]

    async def save_findings(self, thread_id: str, findings: list[Finding]) -> None:
        """Replace this completed run's recorded observations atomically enough to retry."""
        async with self.pool.connection() as conn:
            await conn.execute("DELETE FROM findings WHERE thread_id = %s", (thread_id,))
            for finding in findings:
                await conn.execute(
                    """
                    INSERT INTO findings (
                        id, thread_id, project_id, user_id, rule, slot, mpn, manufacturer,
                        lifecycle, verdict, outcome, action, replacement_mpn, signature, worked
                    )
                    SELECT %s, t.id, t.project_id, t.user_id, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                      FROM threads t
                     WHERE t.id = %s
                    """,
                    (
                        new_id(),
                        finding.rule,
                        finding.slot,
                        finding.mpn,
                        finding.manufacturer,
                        finding.lifecycle,
                        finding.verdict,
                        finding.outcome,
                        finding.action,
                        finding.replacement_mpn,
                        finding.signature,
                        finding.worked,
                        thread_id,
                    ),
                )

    async def precedents_for_user(
        self, user_id: str, signature: str, *, exclude_thread: str, limit: int = 3
    ) -> list[dict[str, Any]]:
        """Successful repairs this user has already made to a structurally identical conflict."""
        async with self.pool.connection() as conn:
            cursor = await conn.cursor(row_factory=dict_row).execute(
                """
                SELECT f.rule, f.action, f.signature, p.name AS project_name
                  FROM findings f
                  JOIN projects p ON p.id = f.project_id AND p.user_id = f.user_id
                 WHERE f.user_id = %s
                   AND f.signature = %s
                   AND f.thread_id <> %s
                   AND f.worked IS TRUE
                   AND f.outcome = 'repaired'
              ORDER BY f.created_at DESC
                 LIMIT %s
                """,
                (user_id, signature, exclude_thread, limit),
            )
            return await cursor.fetchall()

    async def memory_for_user(self, user_id: str, *, part_limit: int) -> dict[str, Any]:
        """The bounded project/part graph, with every read constrained at the boundary."""
        async with self.pool.connection() as conn:
            projects_cursor = await conn.cursor(row_factory=dict_row).execute(
                """
                SELECT p.id, p.name, COUNT(t.id)::integer AS boards
                  FROM projects p
             LEFT JOIN threads t ON t.project_id = p.id AND t.user_id = p.user_id
                 WHERE p.user_id = %s
              GROUP BY p.id, p.name
              ORDER BY p.updated_at DESC
                """,
                (user_id,),
            )
            project_rows = await projects_cursor.fetchall()
            bom_cursor = await conn.cursor(row_factory=dict_row).execute(
                """
                SELECT t.project_id, p.name AS project_name,
                       item->>'mpn' AS mpn, item->>'manufacturer' AS manufacturer,
                       item->>'lifecycle' AS lifecycle
                  FROM threads t
                  JOIN projects p ON p.id = t.project_id AND p.user_id = t.user_id
            CROSS JOIN LATERAL jsonb_array_elements(COALESCE(t.bom, '[]'::jsonb)) AS item
                 WHERE t.user_id = %s AND NULLIF(item->>'mpn', '') IS NOT NULL
                """,
                (user_id,),
            )
            bom_rows = await bom_cursor.fetchall()
            findings_cursor = await conn.cursor(row_factory=dict_row).execute(
                """
                SELECT f.thread_id, f.project_id, p.name AS project_name, f.rule, f.slot,
                       f.mpn, f.manufacturer, f.lifecycle, f.verdict, f.outcome, f.action,
                       f.replacement_mpn
                  FROM findings f
                  JOIN threads t ON t.id = f.thread_id AND t.user_id = f.user_id
                  JOIN projects p ON p.id = f.project_id AND p.user_id = f.user_id
                 WHERE f.user_id = %s
                """,
                (user_id,),
            )
            finding_rows = await findings_cursor.fetchall()
            mpns = sorted(
                {
                    row["mpn"]
                    for row in [*bom_rows, *finding_rows]
                    if isinstance(row["mpn"], str) and row["mpn"]
                }
            )
            fact_rows: list[dict[str, Any]] = []
            if mpns:
                facts_cursor = await conn.cursor(row_factory=dict_row).execute(
                    """
                    SELECT mpn, field, value, source
                      FROM part_facts
                     WHERE mpn = ANY(%s)
                  ORDER BY mpn, field
                    """,
                    (mpns,),
                )
                fact_rows = await facts_cursor.fetchall()

        facts_by_mpn: dict[str, list[dict[str, Any]]] = {}
        for row in fact_rows:
            facts_by_mpn.setdefault(row["mpn"], []).append(
                {"field": row["field"], "value": row["value"], "source": row["source"]}
            )
        parts: dict[str, dict[str, Any]] = {}
        for row in bom_rows:
            part = parts.setdefault(
                row["mpn"],
                {
                    "mpn": row["mpn"],
                    "manufacturer": row["manufacturer"],
                    "lifecycle": row["lifecycle"],
                    "used_in": {},
                    "findings": [],
                    "facts": facts_by_mpn.get(row["mpn"], []),
                },
            )
            if part["manufacturer"] is None:
                part["manufacturer"] = row["manufacturer"]
            if part["lifecycle"] is None:
                part["lifecycle"] = row["lifecycle"]
            part["used_in"][row["project_id"]] = {
                "project_id": row["project_id"],
                "project_name": row["project_name"],
            }
        for row in finding_rows:
            part = parts.setdefault(
                row["mpn"],
                {
                    "mpn": row["mpn"],
                    "manufacturer": row["manufacturer"],
                    "lifecycle": row["lifecycle"],
                    "used_in": {},
                    "findings": [],
                    "facts": facts_by_mpn.get(row["mpn"], []),
                },
            )
            if part["manufacturer"] is None:
                part["manufacturer"] = row["manufacturer"]
            if part["lifecycle"] is None:
                part["lifecycle"] = row["lifecycle"]
            part["findings"].append(
                {
                    "thread_id": row["thread_id"],
                    "project_id": row["project_id"],
                    "project_name": row["project_name"],
                    "rule": row["rule"],
                    "slot": row["slot"],
                    "verdict": row["verdict"],
                    "outcome": row["outcome"],
                    "action": row["action"],
                    "replacement_mpn": row["replacement_mpn"],
                }
            )

        ordered = sorted(
            parts.values(),
            key=lambda part: (-bool(part["findings"]), -len(part["used_in"]), part["mpn"]),
        )
        capped = len(ordered) > part_limit
        return {
            "projects": project_rows,
            "parts": [
                {
                    **part,
                    "used_in": sorted(part["used_in"].values(), key=lambda edge: edge["project_name"]),
                }
                for part in ordered[:part_limit]
            ],
            "parts_capped": capped,
            "part_limit": part_limit,
        }
    async def save_part_facts(
        self, facts: Iterable[tuple[str, str, str, str | None]]
    ) -> None:
        """Upsert board-independent part properties learned during a run."""
        permitted = [
            (mpn, field, value, source)
            for mpn, field, value, source in facts
            if field in DOSSIER_FIELDS and value != ""
        ]
        if not permitted:
            return
        async with self.pool.connection() as conn:
            for mpn, field, value, source in permitted:
                await conn.execute(
                    """
                    INSERT INTO part_facts (mpn, field, value, source)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (mpn, field) DO UPDATE
                       SET value = EXCLUDED.value,
                           source = EXCLUDED.source,
                           observed_at = now()
                    """,
                    (mpn, field, value, source),
                )

    async def part_facts(self, mpns: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        """Known facts for these parts, keyed by MPN."""
        requested = list(dict.fromkeys(mpn for mpn in mpns if mpn))
        if not requested:
            return {}
        async with self.pool.connection() as conn:
            cursor = await conn.cursor(row_factory=dict_row).execute(
                """
                SELECT mpn, field, value, source
                  FROM part_facts
                 WHERE mpn = ANY(%s)
              ORDER BY mpn, field
                """,
                (requested,),
            )
            rows = await cursor.fetchall()
        facts: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            facts.setdefault(row["mpn"], []).append(
                {"field": row["field"], "value": row["value"], "source": row["source"]}
            )
        return facts
