"""The application tables: ownership, sessions, and what has to survive a restart.

Skipped unless `CONTINUITY_TEST_DB` points at a database, so the default suite stays
offline and under a second. Same idiom as `CONTINUITY_LIVE` and `CONTINUITY_FIXTURES`.

    createdb continuity_test
    CONTINUITY_TEST_DB=postgresql:///continuity_test pytest tests/test_store.py

Two properties carry most of the weight here. **A lookup scoped to a user must return
nothing for anyone else** — `/resume` and `/export` authorise on exactly these queries,
and a missing WHERE clause is an IDOR rather than a failing assertion. And **`last_seq`
must round-trip**, because a resumed stream that restarts its numbering is discarded
silently by the client.
"""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from contextlib import asynccontextmanager

import pytest

from continuity.api.store import SESSION_IDLE_SECONDS, SESSION_TTL_SECONDS, EmailTaken, Store
from continuity.api.findings import Finding

DB_URL = os.environ.get("CONTINUITY_TEST_DB")

pytestmark = pytest.mark.skipif(
    not DB_URL, reason="set CONTINUITY_TEST_DB to run the store tests"
)


def run(coro):
    return asyncio.run(coro)


@asynccontextmanager
async def fresh():
    """A store on an empty schema. Truncates rather than dropping, so `setup` runs once."""
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(DB_URL, min_size=1, max_size=2, open=False) as pool:
        await pool.open()
        store = Store(pool)
        await store.setup()
        async with pool.connection() as conn:
            await conn.execute("TRUNCATE users, sessions, projects, threads, part_facts CASCADE")
        yield store


async def a_user(store: Store, email: str = "sparsh@example.com"):
    return await store.create_user(email, "argon2-hash-goes-here")


# ── users ─────────────────────────────────────────────────────────────────────


def test_a_registered_user_is_found_by_email():
    async def go():
        async with fresh() as store:
            created = await a_user(store)
            return created, await store.user_by_email("sparsh@example.com")

    created, found = run(go())
    assert found is not None
    assert found.id == created.id
    assert found.password_hash == "argon2-hash-goes-here"


def test_email_is_folded_so_case_cannot_split_an_account():
    async def go():
        async with fresh() as store:
            await store.create_user("Sparsh@Example.COM", "h")
            return await store.user_by_email("sparsh@example.com")

    assert run(go()) is not None


def test_a_duplicate_email_is_refused():
    async def go():
        async with fresh() as store:
            await a_user(store)
            try:
                await a_user(store)
            except EmailTaken:
                return "refused"
            return "accepted"

    assert run(go()) == "refused"


def test_a_duplicate_is_refused_across_case_too():
    async def go():
        async with fresh() as store:
            await store.create_user("sparsh@example.com", "h")
            try:
                await store.create_user("SPARSH@EXAMPLE.COM", "h")
            except EmailTaken:
                return "refused"
            return "accepted"

    assert run(go()) == "refused"


def test_onboarding_starts_unrecorded_and_is_recorded_once():
    async def go():
        async with fresh() as store:
            user = await a_user(store)
            before = (await store.user_by_id(user.id)).onboarded_at
            await store.mark_onboarded(user.id)
            return before, (await store.user_by_id(user.id)).onboarded_at

    before, after = run(go())
    assert before is None
    assert after is not None


# ── sessions ──────────────────────────────────────────────────────────────────


def test_a_session_resolves_to_its_user():
    async def go():
        async with fresh() as store:
            user = await a_user(store)
            token = await store.create_session(user.id, ttl_seconds=3600)
            return user, await store.user_for_token(token)

    user, resolved = run(go())
    assert resolved is not None
    assert resolved.id == user.id


def test_the_raw_token_never_reaches_the_database():
    """A dump of `sessions` must not be a set of usable cookies."""

    async def go():
        async with fresh() as store:
            user = await a_user(store)
            token = await store.create_session(user.id, ttl_seconds=3600)
            async with store.pool.connection() as conn:
                cursor = await conn.execute("SELECT token_hash FROM sessions")
                rows = await cursor.fetchall()
            return token, [row[0] for row in rows]

    token, stored = run(go())
    assert token not in stored
    assert len(stored) == 1


def test_an_expired_session_resolves_to_nobody():
    async def go():
        async with fresh() as store:
            user = await a_user(store)
            token = await store.create_session(user.id, ttl_seconds=-1)
            return await store.user_for_token(token)

    assert run(go()) is None


def test_using_a_session_slides_its_idle_window_forward():
    """The whole point of an idle timeout: activity is what keeps you signed in."""

    async def go():
        async with fresh() as store:
            user = await a_user(store)
            token = await store.create_session(user.id, ttl_seconds=60)
            async with store.pool.connection() as conn:
                cursor = await conn.execute("SELECT expires_at FROM sessions")
                before = (await cursor.fetchone())[0]

            resolved = await store.user_for_token(token)

            async with store.pool.connection() as conn:
                cursor = await conn.execute("SELECT expires_at FROM sessions")
                after = (await cursor.fetchone())[0]
            return resolved, before, after

    resolved, before, after = run(go())
    assert resolved is not None
    # A minute was granted, the full idle window is now in front of it.
    assert (after - before).total_seconds() > SESSION_IDLE_SECONDS - 120


def test_the_absolute_ceiling_survives_being_slid_against():
    """Otherwise a cookie used once every twenty minutes never expires at all."""

    async def go():
        async with fresh() as store:
            user = await a_user(store)
            token = await store.create_session(user.id, ttl_seconds=60)
            # Backdate creation to just inside the ceiling: the renewal must clamp to it
            # rather than granting another full idle window past it.
            async with store.pool.connection() as conn:
                await conn.execute(
                    "UPDATE sessions SET created_at = now() - %s + interval '2 minutes'",
                    (timedelta(seconds=SESSION_TTL_SECONDS),),
                )
            resolved = await store.user_for_token(token)
            async with store.pool.connection() as conn:
                cursor = await conn.execute(
                    "SELECT expires_at - now() FROM sessions"
                )
                remaining = (await cursor.fetchone())[0]
            return resolved, remaining

    resolved, remaining = run(go())
    assert resolved is not None
    assert remaining.total_seconds() < 3 * 60, "clamped to the ceiling, not extended past it"


def test_a_session_past_the_absolute_ceiling_resolves_to_nobody():
    async def go():
        async with fresh() as store:
            user = await a_user(store)
            token = await store.create_session(user.id, ttl_seconds=3600)
            async with store.pool.connection() as conn:
                await conn.execute(
                    "UPDATE sessions SET created_at = now() - %s",
                    (timedelta(seconds=SESSION_TTL_SECONDS + 60),),
                )
            return await store.user_for_token(token)

    assert run(go()) is None, "still inside its idle window, but too old to renew"


def test_a_deleted_session_resolves_to_nobody():
    async def go():
        async with fresh() as store:
            user = await a_user(store)
            token = await store.create_session(user.id, ttl_seconds=3600)
            await store.delete_session(token)
            return await store.user_for_token(token)

    assert run(go()) is None


def test_an_unknown_token_resolves_to_nobody():
    async def go():
        async with fresh() as store:
            await a_user(store)
            return await store.user_for_token("not-a-real-token")

    assert run(go()) is None


# ── projects and threads: ownership ───────────────────────────────────────────


def test_projects_list_for_their_owner_only():
    async def go():
        async with fresh() as store:
            mine = await a_user(store, "mine@example.com")
            theirs = await a_user(store, "theirs@example.com")
            await store.create_project(mine.id, "My board")
            await store.create_project(theirs.id, "Their board")
            return (
                await store.projects_for_user(mine.id),
                await store.projects_for_user(theirs.id),
            )

    ours, others = run(go())
    assert [p.name for p in ours] == ["My board"]
    assert [p.name for p in others] == ["Their board"]


def test_a_project_is_invisible_to_another_user():
    async def go():
        async with fresh() as store:
            mine = await a_user(store, "mine@example.com")
            theirs = await a_user(store, "theirs@example.com")
            project = await store.create_project(mine.id, "My board")
            return (
                await store.project_for_user(project.id, mine.id),
                await store.project_for_user(project.id, theirs.id),
            )

    owned, stolen = run(go())
    assert owned is not None
    assert stolen is None


def test_a_thread_is_invisible_to_another_user():
    """This lookup is what stands between `/resume` and an IDOR."""

    async def go():
        async with fresh() as store:
            mine = await a_user(store, "mine@example.com")
            theirs = await a_user(store, "theirs@example.com")
            project = await store.create_project(mine.id, "My board")
            await store.create_thread("thread-1", project.id, mine.id, "a brief")
            return (
                await store.thread_for_user("thread-1", mine.id),
                await store.thread_for_user("thread-1", theirs.id),
            )

    owned, stolen = run(go())
    assert owned is not None
    assert owned.prompt == "a brief"
    assert stolen is None


# ── what has to survive a restart ─────────────────────────────────────────────


def test_a_new_thread_starts_at_minus_one():
    async def go():
        async with fresh() as store:
            user = await a_user(store)
            project = await store.create_project(user.id, "P")
            await store.create_thread("thread-1", project.id, user.id, "a brief")
            return await store.thread_for_user("thread-1", user.id)

    thread = run(go())
    assert thread.last_seq == -1
    assert thread.status == "running"


def test_progress_round_trips():
    async def go():
        async with fresh() as store:
            user = await a_user(store)
            project = await store.create_project(user.id, "P")
            await store.create_thread("thread-1", project.id, user.id, "a brief")
            await store.save_progress("thread-1", last_seq=41, status="awaiting")
            return await store.thread_for_user("thread-1", user.id)

    thread = run(go())
    assert thread.last_seq == 41
    assert thread.status == "awaiting"


def test_abandoned_is_an_accepted_terminal_status():
    async def go():
        async with fresh() as store:
            user = await a_user(store)
            project = await store.create_project(user.id, "P")
            await store.create_thread("thread-1", project.id, user.id, "a brief")
            await store.save_progress("thread-1", last_seq=0, status="abandoned")
            return await store.thread_for_user("thread-1", user.id)

    assert run(go()).status == "abandoned"


def test_the_bom_round_trips():
    async def go():
        async with fresh() as store:
            user = await a_user(store)
            project = await store.create_project(user.id, "P")
            await store.create_thread("thread-1", project.id, user.id, "a brief")
            rows = [{"slot": "reg", "mpn": "AMS1117-3.3", "qty": 1, "unit_price": 0.12}]
            await store.save_bom("thread-1", rows)
            return await store.thread_for_user("thread-1", user.id)

    thread = run(go())
    assert thread.bom == [{"slot": "reg", "mpn": "AMS1117-3.3", "qty": 1, "unit_price": 0.12}]


def test_run_events_round_trip_in_sequence_without_duplicates():
    async def go():
        async with fresh() as store:
            user = await a_user(store)
            project = await store.create_project(user.id, "P")
            await store.create_thread("thread-1", project.id, user.id, "a brief")
            await store.save_run_events(
                "thread-1",
                [
                    {"type": "reasoning", "seq": 2, "thread_id": "thread-1", "text": "later"},
                    {"type": "reasoning", "seq": 1, "thread_id": "thread-1", "text": "first"},
                ],
            )
            await store.save_run_events(
                "thread-1",
                [{"type": "reasoning", "seq": 1, "thread_id": "thread-1", "text": "duplicate"}],
            )
            return await store.run_events("thread-1")

    assert [event["text"] for event in run(go())] == ["first", "later"]


def test_part_facts_upsert_in_place_and_return_by_mpn():
    async def go():
        async with fresh() as store:
            await store.save_part_facts([("TPS54331DR", "theta_ja", "116.3", "TI datasheet")])
            await store.save_part_facts([("TPS54331DR", "theta_ja", "62.0", "newer TI datasheet")])
            return await store.part_facts(["TPS54331DR"])

    assert run(go()) == {
        "TPS54331DR": [
            {"field": "theta_ja", "value": "62.0", "source": "newer TI datasheet"}
        ]
    }


def test_an_empty_part_facts_lookup_does_not_open_a_connection():
    class NoQueryPool:
        def connection(self):
            raise AssertionError("an empty MPN list must not query the database")

    assert run(Store(NoQueryPool()).part_facts([])) == {}


def test_memory_includes_stable_facts_and_empty_lists_for_unknown_parts():
    async def go():
        async with fresh() as store:
            user = await a_user(store)
            project = await store.create_project(user.id, "P")
            await store.create_thread("thread-1", project.id, user.id, "a brief")
            await store.save_bom(
                "thread-1",
                [
                    {"slot": "reg", "mpn": "TPS54331DR", "qty": 1},
                    {"slot": "sensor", "mpn": "SHT40", "qty": 1},
                ],
            )
            await store.save_part_facts([("TPS54331DR", "theta_ja", "62.0", "TI datasheet")])
            return await store.memory_for_user(user.id, part_limit=100)

    parts = {part["mpn"]: part for part in run(go())["parts"]}
    assert parts["TPS54331DR"]["facts"] == [
        {"field": "theta_ja", "value": "62.0", "source": "TI datasheet"}
    ]
    assert parts["SHT40"]["facts"] == []


def test_an_unknown_status_is_refused_by_the_database():
    """The status vocabulary is fixed, the same way every other vocabulary here is."""

    async def go():
        async with fresh() as store:
            user = await a_user(store)
            project = await store.create_project(user.id, "P")
            await store.create_thread("thread-1", project.id, user.id, "a brief")
            try:
                await store.save_progress("thread-1", last_seq=0, status="banana")
            except Exception as exc:
                return type(exc).__name__
            return "accepted"

    assert run(go()) != "accepted"


# ── what the engine reported ──────────────────────────────────────────────────


def test_a_thread_that_has_not_finished_has_no_summary():
    async def go():
        async with fresh() as store:
            user = await a_user(store)
            project = await store.create_project(user.id, "P")
            await store.create_thread("thread-1", project.id, user.id, "a brief")
            return await store.thread_for_user("thread-1", user.id)

    assert run(go()).summary is None


def test_the_summary_round_trips_verbatim():
    """Stored as the engine emitted it — this is a record, not a recomputation."""

    async def go():
        async with fresh() as store:
            user = await a_user(store)
            project = await store.create_project(user.id, "P")
            await store.create_thread("thread-1", project.id, user.id, "a brief")
            await store.save_summary(
                "thread-1",
                {"slots": 4, "placed": 4, "conflicts_resolved": 3, "elapsed_s": 12.4},
            )
            return await store.thread_for_user("thread-1", user.id)

    thread = run(go())
    assert thread.summary == {
        "slots": 4,
        "placed": 4,
        "conflicts_resolved": 3,
        "elapsed_s": 12.4,
    }


def test_precedents_are_scoped_to_successful_other_user_threads():
    async def go():
        async with fresh() as store:
            mine = await a_user(store, "mine@example.com")
            theirs = await a_user(store, "theirs@example.com")
            first = await store.create_project(mine.id, "First")
            second = await store.create_project(mine.id, "Second")
            signature = "thermal_dissipation|regulator|linear|pkg:SOT|drop:>=8V|load:100-500mA"
            for thread_id, project, worked in (
                ("thread-one", first, True),
                ("thread-two", second, True),
                ("thread-false", first, False),
            ):
                await store.create_thread(thread_id, project.id, mine.id, "brief")
                await store.save_findings(
                    thread_id,
                    [
                        Finding(
                            "thermal_dissipation",
                            "regulator",
                            "BAD-LDO",
                            "Too hot.",
                            outcome="repaired",
                            action="change_topology",
                            signature=signature,
                            worked=worked,
                        )
                    ],
                )
            return (
                await store.precedents_for_user(mine.id, signature, exclude_thread="thread-two"),
                await store.precedents_for_user(mine.id, signature, exclude_thread="thread-one"),
                await store.precedents_for_user(theirs.id, signature, exclude_thread="thread-one"),
            )

    from_first, from_second, other_user = run(go())
    assert from_first == [
        {
            "rule": "thermal_dissipation",
            "action": "change_topology",
            "signature": "thermal_dissipation|regulator|linear|pkg:SOT|drop:>=8V|load:100-500mA",
            "project_name": "First",
        }
    ]
    assert from_second == [
        {
            "rule": "thermal_dissipation",
            "action": "change_topology",
            "signature": "thermal_dissipation|regulator|linear|pkg:SOT|drop:>=8V|load:100-500mA",
            "project_name": "Second",
        }
    ]
    assert other_user == []


def test_a_phantom_thread_never_masks_the_run_that_did_the_work():
    """React's double-invoke starts two runs; only one of them ever emits anything.

    Found live: a project waiting on a question opened to "this run has no board to
    restore", because the caller took the newest thread and the newest was the cancelled
    twin — `abandoned`, `last_seq = -1`, no checkpoint, no frames.
    """

    async def go():
        async with fresh() as store:
            user = await a_user(store)
            project = await store.create_project(user.id, "P")
            await store.create_thread("real", project.id, user.id, "a brief")
            await store.create_thread("phantom", project.id, user.id, "a brief")
            await store.save_progress("real", 75, "awaiting")
            await store.save_progress("phantom", -1, "abandoned")
            return await store.threads_for_project(project.id, user.id)

    assert [thread.id for thread in run(go())][0] == "real"


def test_a_live_run_outranks_a_finished_one():
    async def go():
        async with fresh() as store:
            user = await a_user(store)
            project = await store.create_project(user.id, "P")
            await store.create_thread("older-live", project.id, user.id, "a brief")
            await store.create_thread("newer-done", project.id, user.id, "a brief")
            await store.save_progress("older-live", 4, "running")
            await store.save_progress("newer-done", 90, "done")
            return await store.threads_for_project(project.id, user.id)

    assert [thread.id for thread in run(go())][0] == "older-live"
