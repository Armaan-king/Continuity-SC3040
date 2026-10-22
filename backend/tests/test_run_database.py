"""Real PostgreSQL checks for ownership, migration repeatability, and stale-worker fencing."""

import asyncio

import pytest
import psycopg

from continuity.api.store import execution_lease
from tests.test_store import fresh, a_user, DB_URL

pytestmark = pytest.mark.skipif(not DB_URL, reason="requires disposable CONTINUITY_TEST_DB")


def test_migrations_repeat_and_owner_constraints_are_enforced():
    async def go():
        async with fresh() as store:
            await store.setup()
            alice = await a_user(store)
            bob = await a_user(store, "bob@example.com")
            project = await store.create_project(alice.id, "A board")
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                await store.create_thread("wrong-owner", project.id, bob.id, "USB sensor")
            async with store.pool.connection() as conn:
                cursor = await conn.execute("SELECT count(*) FROM schema_migrations")
                assert (await cursor.fetchone())[0] == 4
    asyncio.run(go())


def test_only_current_lease_can_write_and_replay_is_owner_scoped():
    async def go():
        async with fresh() as store:
            alice = await a_user(store)
            project = await store.create_project(alice.id, "A board")
            await store.create_thread("run", project.id, alice.id, "USB sensor")
            assert await store.claim_run("run", alice.id, "old")
            assert not await store.claim_run("run", alice.id, "duplicate")
            event = {"type": "reasoning", "thread_id": "run", "seq": 0, "text": "Started"}
            await store.append_stream_event("run", "old", event)
            assert await store.events_after("run", "stranger", -1) == []
            assert await store.events_after("run", alice.id, -1) == [event]
            async with store.pool.connection() as conn:
                await conn.execute("UPDATE threads SET lease_until = now() - interval '1 second' WHERE id = 'run'")
            await store.recover_stale_runs()
            assert (await store.thread_for_user("run", alice.id)).status == "abandoned"
            assert await store.claim_run("run", alice.id, "new")
            context = execution_lease.set(("run", "old"))
            try:
                with pytest.raises(RuntimeError, match="lease"):
                    await store.save_progress("run", 99, "done")
            finally:
                execution_lease.reset(context)
            await store.release_run("run", "old", "done")
            assert (await store.thread_for_user("run", alice.id)).status == "running"
            await store.release_run("run", "new", "awaiting")
            assert (await store.thread_for_user("run", alice.id)).status == "awaiting"
    asyncio.run(go())
