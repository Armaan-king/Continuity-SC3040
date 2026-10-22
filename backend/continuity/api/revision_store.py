"""Revision and stage persistence, sharing the application's pool and lease fence."""
from __future__ import annotations

import uuid
from psycopg.rows import dict_row
from psycopg.types.json import Json
from ..designs.service import digest


async def commit_revision(conn, thread_id: str, document: dict, reason: str, expected: str | None = None) -> str:
    cursor = await conn.execute("SELECT project_id, user_id FROM threads WHERE id = %s", (thread_id,))
    owner = await cursor.fetchone()
    if owner is None:
        raise ValueError("Unknown thread")
    project_id, user_id = owner
    if reason == "Run completed":
        cursor = await conn.execute("SELECT event FROM run_events WHERE thread_id = %s AND event->>'type' IN ('decision', 'comparison') ORDER BY seq", (thread_id,))
        document = {**document, "decisions": [row[0] for row in await cursor.fetchall()]}
    cursor = await conn.execute("SELECT revision_id FROM projects WHERE id = %s FOR UPDATE", (project_id,))
    parent = (await cursor.fetchone())[0]
    if expected is not None and parent != expected:
        raise ValueError("The design changed. Reload before saving this revision.")
    content_hash = digest(document)
    cursor = await conn.execute("SELECT id FROM design_revisions WHERE thread_id = %s AND content_hash = %s", (thread_id, content_hash))
    existing = await cursor.fetchone()
    revision_id = existing[0] if existing else uuid.uuid4().hex
    if not existing:
        await conn.execute("""INSERT INTO design_revisions
            (id, project_id, user_id, thread_id, parent_id, content_hash, document, reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (revision_id, project_id, user_id, thread_id, parent, content_hash, Json(document), reason))
    findings = document["report"]["findings"]
    summary = {"slots": len(document["board"]["slots"]), "placed": len(document["bom"]["rows"]),
               "failures": sum(f["outcome"] == "FAIL" and not f["accepted"] for f in findings),
               "warnings": sum(f["severity"] == "warning" and f["outcome"] != "UNKNOWN" or f["accepted"] for f in findings),
               "unchecked": sum(f["outcome"] == "UNKNOWN" for f in findings)}
    await conn.execute("UPDATE threads SET revision_id = %s, bom = %s, summary = '{\"conflicts_resolved\": 0, \"elapsed_s\": 0, \"stopped\": false}'::jsonb || COALESCE(summary, '{}'::jsonb) || %s::jsonb WHERE id = %s",
                       (revision_id, Json(document["bom"]["rows"]), Json(summary), thread_id))
    await conn.execute("UPDATE projects SET revision_id = %s, updated_at = now() WHERE id = %s", (revision_id, project_id))
    return revision_id


class RevisionRepository:
    async def revision_for_user(self, revision_id: str, user_id: str) -> dict | None:
        async with self.pool.connection() as conn:
            cursor = await conn.cursor(row_factory=dict_row).execute(
                "SELECT * FROM design_revisions WHERE id = %s AND user_id = %s", (revision_id, user_id))
            return await cursor.fetchone()

    async def thread_revision(self, thread_id: str, user_id: str) -> dict | None:
        async with self.pool.connection() as conn:
            cursor = await conn.cursor(row_factory=dict_row).execute("""
                SELECT r.* FROM threads t JOIN design_revisions r ON r.id = t.revision_id
                WHERE t.id = %s AND t.user_id = %s AND r.user_id = %s""", (thread_id, user_id, user_id))
            return await cursor.fetchone()

    async def revisions_for_project(self, project_id: str, user_id: str) -> list[dict]:
        async with self.pool.connection() as conn:
            cursor = await conn.cursor(row_factory=dict_row).execute("""
                SELECT r.id, r.thread_id, r.parent_id, r.reason, r.created_at,
                    r.document->'report'->'counts' AS counts, r.document->'bom'->'total' AS total,
                    (p.revision_id = r.id) AS active
                FROM design_revisions r JOIN projects p ON p.id = r.project_id
                WHERE r.project_id = %s AND r.user_id = %s ORDER BY r.created_at DESC, r.id DESC LIMIT 100
                """, (project_id, user_id))
            return await cursor.fetchall()

    async def save_revision(self, thread_id: str, user_id: str, document: dict, reason: str, expected: str) -> str:
        async with self.pool.connection() as conn:
            async with conn.transaction():
                # Same account lock order as run admission; no edit can race a run.
                await conn.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,))
                cursor = await conn.execute("SELECT id FROM threads WHERE user_id = %s AND lease_until > now()", (user_id,))
                if await cursor.fetchone():
                    raise ValueError("Wait for the active run to finish before editing a revision.")
                cursor = await conn.execute("SELECT id FROM threads WHERE id = %s AND user_id = %s AND status = 'done' FOR UPDATE", (thread_id, user_id))
                if await cursor.fetchone() is None:
                    raise ValueError("Only a completed design can be revised.")
                return await commit_revision(conn, thread_id, document, reason, expected)

    async def save_run_input(self, thread_id: str, kind: str, payload: dict) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("INSERT INTO run_inputs (thread_id, kind, payload) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                               (thread_id, kind, Json(payload)))

    async def run_input(self, thread_id: str, user_id: str) -> dict | None:
        async with self.pool.connection() as conn:
            cursor = await conn.cursor(row_factory=dict_row).execute("""
                SELECT i.* FROM run_inputs i JOIN threads t ON t.id = i.thread_id
                WHERE t.id = %s AND t.user_id = %s""", (thread_id, user_id))
            return await cursor.fetchone()

    async def stage_result(self, thread_id: str, key: str):
        async with self._run_connection(thread_id) as conn:
            cursor = await conn.execute("SELECT result FROM run_stages WHERE thread_id = %s AND stage_key = %s", (thread_id, key))
            row = await cursor.fetchone()
            return row[0] if row else None

    async def save_stage(self, thread_id: str, key: str, result: dict) -> None:
        async with self._run_connection(thread_id) as conn:
            await conn.execute("INSERT INTO run_stages (thread_id, stage_key, result) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                               (thread_id, key, Json(result)))

    async def reserve_research(self, thread_id: str, key: str, limit: int, budget: float) -> bool:
        async with self._run_connection(thread_id) as conn:
            await conn.execute("SELECT id FROM threads WHERE id = %s FOR UPDATE", (thread_id,))
            cursor = await conn.execute("SELECT count(*), COALESCE(sum(GREATEST(cost_usd, 0.02)), 0) FROM research_requests WHERE thread_id = %s", (thread_id,))
            count, spent = await cursor.fetchone()
            if count >= limit or float(spent) + 0.02 > budget + 1e-9:
                return False
            cursor = await conn.execute("INSERT INTO research_requests (thread_id, request_key, status) VALUES (%s, %s, 'reserved') ON CONFLICT DO NOTHING", (thread_id, key))
            return cursor.rowcount == 1

    async def complete_research(self, thread_id: str, key: str, result: dict) -> None:
        async with self._run_connection(thread_id) as conn:
            await conn.execute("UPDATE research_requests SET status = %s, result = %s, cost_usd = %s WHERE thread_id = %s AND request_key = %s",
                               ("failed" if result.get("error") else "complete", Json(result), result.get("cost_usd", 0), thread_id, key))

    async def research_result(self, thread_id: str, key: str) -> dict | None:
        async with self._run_connection(thread_id) as conn:
            cursor = await conn.execute("SELECT result FROM research_requests WHERE thread_id = %s AND request_key = %s", (thread_id, key))
            row = await cursor.fetchone()
            return row[0] if row else None
