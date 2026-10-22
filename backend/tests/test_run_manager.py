"""Execution lifetime is independent of any subscriber's connection."""

import asyncio
import json

import pytest
from fastapi import HTTPException

from fuze.api.events import EventStream, frame
from fuze.api.runs import RunManager


def test_disconnect_reconnect_and_explicit_cancel():
    async def go():
        manager = RunManager()
        proceed = asyncio.Event()
        stopped = asyncio.Event()
        stream = EventStream("run")

        async def produce():
            try:
                yield frame(stream.reasoning(None, "one"))
                await proceed.wait()
                yield frame(stream.reasoning(None, "two"))
                await asyncio.Event().wait()
            finally:
                stopped.set()

        await manager.start("run", "owner", produce())
        subscription = manager.subscribe("run", "owner")
        first = await anext(subscription)
        assert '"seq":0' in first
        await subscription.aclose()
        assert not stopped.is_set()
        proceed.set()
        resumed = manager.subscribe("run", "owner", after=0)
        assert '"seq":1' in await anext(resumed)
        assert not await manager.stop("run", "stranger")
        assert await manager.stop("run", "owner")
        assert stopped.is_set()
        await resumed.aclose()
        await manager.close()
    asyncio.run(go())


def test_duplicate_execution_and_capacity_are_bounded():
    async def go():
        manager = RunManager(capacity=2)
        async def produce():
            await asyncio.Event().wait()
            yield ""
        await manager.start("a", "alice", produce())
        with pytest.raises(HTTPException) as duplicate:
            await manager.start("b", "alice", produce())
        assert duplicate.value.status_code == 409
        await manager.start("c", "bob", produce())
        with pytest.raises(HTTPException) as busy:
            await manager.start("d", "carol", produce())
        assert busy.value.status_code == 429
        await asyncio.sleep(0)
        await manager.close()
    asyncio.run(go())


def test_timeout_ends_stream_with_sanitized_monotonic_error():
    async def go():
        manager = RunManager(timeout=0.03)
        async def produce():
            await asyncio.Event().wait()
            yield ""
        await manager.start("continued", "owner", produce(), last_seq=25)
        frames = [item async for item in manager.subscribe("continued", "owner", after=25)]
        event = json.loads(frames[-1].removeprefix("data: "))
        assert event["type"] == "error"
        assert event["seq"] == 26
        assert not event["recoverable"]
        assert manager.runs["continued"].finished
    asyncio.run(go())


def test_question_is_not_visible_until_producer_has_persisted_the_pause():
    async def go():
        manager = RunManager()
        pending = asyncio.Event()
        persisted = asyncio.Event()
        stream = EventStream("pause")
        async def produce():
            yield frame(stream.question("supply", "Which supply?", ["USB"]))
            pending.set()
            await persisted.wait()
        await manager.start("pause", "owner", produce())
        await pending.wait()
        assert manager.runs["pause"].frames == []
        subscription = manager.subscribe("pause", "owner")
        delivery = asyncio.create_task(anext(subscription))
        await asyncio.sleep(0)
        assert not delivery.done()
        persisted.set()
        assert '"type":"question"' in await delivery
        # A user can answer immediately without the previous run occupying admission.
        manager.admit("owner")
        await subscription.aclose()
        await manager.close()
    asyncio.run(go())


@pytest.mark.parametrize("disconnect_first_request", [False, True])
def test_repeated_stop_waits_for_cleanup_without_cancelling_it_again(disconnect_first_request):
    async def go():
        manager = RunManager()
        entered = asyncio.Event()
        cleaning = asyncio.Event()
        finish = asyncio.Event()
        cleaned = asyncio.Event()

        async def produce():
            try:
                entered.set()
                await asyncio.Event().wait()
                yield ""
            finally:
                cleaning.set()
                await finish.wait()
                cleaned.set()

        await manager.start("old", "owner", produce())
        await entered.wait()
        first = asyncio.create_task(manager.stop("old", "owner"))
        await cleaning.wait()
        if disconnect_first_request:
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
        second = asyncio.create_task(manager.stop("old", "owner"))
        await asyncio.sleep(0)
        assert not second.done()
        with pytest.raises(HTTPException):
            manager.admit("owner")
        finish.set()
        assert await second
        await asyncio.gather(first, return_exceptions=True)
        assert cleaned.is_set()
        assert manager.runs["old"].finished
        manager.admit("owner")
        assert await manager.stop("old", "owner")  # a retry is idempotent
        await manager.close()
    asyncio.run(go())


def test_producer_close_failure_does_not_leave_account_occupied():
    async def go():
        manager = RunManager()
        entered = asyncio.Event()

        class Producer:
            def __aiter__(self):
                return self

            async def __anext__(self):
                entered.set()
                await asyncio.Event().wait()

            async def aclose(self):
                raise RuntimeError("cleanup failure")

        await manager.start("broken", "owner", Producer())
        await entered.wait()
        assert await manager.stop("broken", "owner")
        assert manager.runs["broken"].finished
        manager.admit("owner")
        await manager.close()
    asyncio.run(go())


def test_revoked_lease_during_frame_save_releases_admission_without_error_logs(caplog):
    import logging
    from fuze.api.store import RunLeaseLost

    async def go():
        manager = RunManager()
        closed = asyncio.Event()
        released = asyncio.Event()

        class RevokedStore:
            async def claim_run(self, *args, **kwargs):
                return True

            async def append_stream_event(self, *args):
                raise RunLeaseLost("Run lease was lost")

            async def release_run(self, *args):
                released.set()

        async def produce():
            try:
                yield 'data: {"type":"reasoning","seq":0,"text":"started"}\n\n'
            finally:
                closed.set()

        await manager.start("revoked", "owner", produce(), RevokedStore())
        await manager.runs["revoked"].task
        assert closed.is_set() and released.is_set()
        assert manager.runs["revoked"].finished
        manager.admit("owner")
        await manager.close()

    with caplog.at_level(logging.WARNING):
        asyncio.run(go())
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
