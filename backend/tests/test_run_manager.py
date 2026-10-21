"""Execution lifetime is independent of any subscriber's connection."""

import asyncio
import json

import pytest
from fastapi import HTTPException

from continuity.api.events import EventStream, frame
from continuity.api.runs import RunManager


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
