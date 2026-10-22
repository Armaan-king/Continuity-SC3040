"""Real PostgreSQL checks for ownership, migration repeatability, and stale-worker fencing."""

import asyncio

import pytest
import psycopg

from fuze.api.store import execution_lease
from tests.test_store import fresh, a_user, DB_URL

pytestmark = pytest.mark.skipif(not DB_URL, reason="requires disposable FUZE_TEST_DB")


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
                assert (await cursor.fetchone())[0] == 5
    asyncio.run(go())


def test_brand_migration_updates_only_the_generated_walkthrough():
    from fuze.api.migrations import MIGRATIONS

    async def go():
        async with fresh() as store:
            user = await a_user(store)
            walkthrough = await store.create_project(user.id, "Welcome to Continuity", is_walkthrough=True)
            personal = await store.create_project(user.id, "Welcome to Continuity")
            renamed = await store.create_project(user.id, "My example", is_walkthrough=True)
            token = await store.create_session(user.id)
            async with store.pool.connection() as conn:
                await conn.execute((MIGRATIONS / "005_fuze_brand.sql").read_text())
                await conn.execute((MIGRATIONS / "005_fuze_brand.sql").read_text())
                result = await conn.execute("SELECT id, name FROM projects WHERE user_id = %s", (user.id,))
                names = dict(await result.fetchall())
            assert names == {walkthrough.id: "Welcome to FUZE", personal.id: "Welcome to Continuity", renamed.id: "My example"}
            assert (await store.user_for_token(token)).id == user.id

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


def test_cancel_revokes_lease_and_fences_recovery_until_explicit_continuation():
    async def go():
        async with fresh() as store:
            alice = await a_user(store)
            bob = await a_user(store, "bob@example.com")
            project = await store.create_project(alice.id, "A board")
            await store.create_thread("old", project.id, alice.id, "sensor", run_kind="brief",
                                      run_payload={"prompt": "sensor"})
            assert await store.claim_run("old", alice.id, "worker")
            assert not await store.cancel_run("old", bob.id)
            assert await store.renew_run("old", "worker")
            assert await store.cancel_run("old", alice.id)
            assert await store.cancel_run("old", alice.id)
            assert not await store.renew_run("old", "worker")
            await store.release_run("old", "worker", "done")
            assert (await store.thread_for_user("old", alice.id)).status == "abandoned"
            # Even if recovery selected this row before Stop, admission must reject it.
            assert not await store.claim_run("old", alice.id, "stale-recovery")
            await store.create_thread("new", project.id, alice.id, "new sensor")
            assert await store.claim_run("new", alice.id, "new-worker")
            await store.release_run("new", "new-worker", "done")
            assert await store.claim_run("old", alice.id, "manual", allow_cancelled=True)
            assert not (await store.run_input("old", alice.id))["cancelled"]
            await store.release_run("old", "manual", "awaiting")
            assert await store.cancel_run("old", alice.id)
            assert (await store.thread_for_user("old", alice.id)).status == "abandoned"
            assert await store.claim_run("old", alice.id, "manual-again", allow_cancelled=True)
            await store.release_run("old", "manual-again", "done")
            assert await store.cancel_run("old", alice.id)
            assert (await store.thread_for_user("old", alice.id)).status == "done"
    asyncio.run(go())
