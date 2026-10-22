"""The terminal database state of a streamed run."""

from __future__ import annotations

import asyncio
import logging
import pytest

from continuity.api import app as app_module
from continuity.api.app import _run
from continuity.api.events import EventStream


class RecordingStore:
    def __init__(self) -> None:
        self.progress: list[tuple[str, int, str]] = []
        self.events: list[tuple[str, list[dict]]] = []
        self.progress_saved = asyncio.Event()

    async def save_progress(self, thread_id: str, last_seq: int, status: str) -> None:
        await asyncio.sleep(0)
        self.progress.append((thread_id, last_seq, status))
        self.progress_saved.set()

    async def save_summary(self, thread_id: str, summary: dict) -> None:
        pass

    async def save_findings(self, thread_id: str, findings: list) -> None:
        pass

    async def save_run_events(self, thread_id: str, events: list[dict]) -> None:
        self.events.append((thread_id, events))

    async def save_bom(self, thread_id: str, rows: list[dict]) -> None:
        pass


class BlockingGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def astream(self, payload, config, stream_mode):
        stream = config["configurable"]["events"]
        self.started.set()
        yield "custom", stream.reasoning(None, "started")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class DoneGraph:
    async def astream(self, payload, config, stream_mode):
        stream = config["configurable"]["events"]
        yield "custom", stream.done(slots=1, conflicts_resolved=0, elapsed_s=0)


class BrokenGraph:
    async def astream(self, payload, config, stream_mode):
        raise RuntimeError("the graph broke")
        yield  # pragma: no cover - makes this an async generator


async def _wait_for_progress(store: RecordingStore) -> None:
    await asyncio.wait_for(store.progress_saved.wait(), timeout=1)


def test_cancelling_a_stream_records_it_as_abandoned():
    async def go():
        thread_id = "cancelled-run"
        graph = BlockingGraph()
        store = RecordingStore()
        app_module.STREAMS[thread_id] = EventStream(thread_id)
        run = _run(graph, thread_id, {"prompt": "brief"}, store)
        await anext(run)
        await graph.started.wait()
        await run.aclose()
        await graph.cancelled.wait()
        await _wait_for_progress(store)
        return store.progress

    assert asyncio.run(go()) == [("cancelled-run", 0, "abandoned")]


def test_a_completed_stream_records_done():
    async def go():
        thread_id = "finished-run"
        store = RecordingStore()
        app_module.STREAMS[thread_id] = EventStream(thread_id)
        assert [item async for item in _run(DoneGraph(), thread_id, {"prompt": "brief"}, store)]
        await _wait_for_progress(store)
        return store.progress

    assert asyncio.run(go()) == [("finished-run", 0, "done")]


def test_a_broken_stream_records_error():
    async def go():
        thread_id = "broken-run"
        store = RecordingStore()
        app_module.STREAMS[thread_id] = EventStream(thread_id)
        assert [item async for item in _run(BrokenGraph(), thread_id, {"prompt": "brief"}, store)]
        await _wait_for_progress(store)
        return store.progress

    assert asyncio.run(go()) == [("broken-run", 0, "error")]


class BomGraph:
    async def astream(self, payload, config, stream_mode):
        stream = config["configurable"]["events"]
        yield "custom", stream.reasoning(None, "before BOM")
        yield "custom", stream.bom([], 0)
        yield "custom", stream.done(slots=0, conflicts_resolved=0, elapsed_s=0)


def test_a_run_persists_non_bom_trace_frames():
    async def go():
        thread_id = "trace-run"
        store = RecordingStore()
        app_module.STREAMS[thread_id] = EventStream(thread_id)
        assert [item async for item in _run(BomGraph(), thread_id, {"prompt": "brief"}, store)]
        await _wait_for_progress(store)
        return store.events

    saved = asyncio.run(go())
    assert [event["type"] for event in saved[0][1]] == ["reasoning", "done"]


def test_trace_persistence_failure_does_not_break_the_run():
    class FailingTraceStore(RecordingStore):
        async def save_run_events(self, thread_id: str, events: list[dict]) -> None:
            raise RuntimeError("database unavailable")

    async def go():
        thread_id = "trace-failure"
        store = FailingTraceStore()
        app_module.STREAMS[thread_id] = EventStream(thread_id)
        frames = [item async for item in _run(DoneGraph(), thread_id, {"prompt": "brief"}, store)]
        await _wait_for_progress(store)
        return frames, store.progress

    frames, progress = asyncio.run(go())
    assert '"type":"done"' in frames[-1]
    assert progress == [("trace-failure", 0, "done")]


@pytest.mark.parametrize("kind", ["brief", "bom"])
def test_stop_while_persisting_a_yielded_frame_closes_in_the_owning_task(kind, monkeypatch, caplog):
    from continuity.api import bom
    from continuity.api.execution import _run_bom
    from continuity.api.runs import RunManager
    from continuity.api.store import RunLeaseLost

    async def go():
        entered = asyncio.Event()
        manager = RunManager()
        graph = BlockingGraph()

        class LeaseStore(RecordingStore):
            revoked = False

            async def claim_run(self, *args, **kwargs):
                return True

            async def renew_run(self, *args):
                return not self.revoked

            async def release_run(self, *args):
                pass

            async def append_stream_event(self, *args):
                entered.set()
                await asyncio.Event().wait()

            async def save_run_events(self, *args):
                if self.revoked:
                    raise RunLeaseLost("Run lease was lost")
                await super().save_run_events(*args)

        store = LeaseStore()
        stream = EventStream(f"cancel-{kind}")
        app_module.STREAMS[stream.thread_id] = stream

        async def validate_bom(*args):
            yield stream.reasoning(None, "started")
            await asyncio.Event().wait()

        monkeypatch.setattr(bom, "validate_bom", validate_bom)
        producer = _run(graph, stream.thread_id, {}, store) if kind == "brief" else _run_bom([], None, stream, store)
        await manager.start(stream.thread_id, "owner", producer, store)
        await entered.wait()  # generator is suspended at yield; worker is in the store
        store.revoked = True
        assert await manager.stop(stream.thread_id, "owner")
        assert manager.runs[stream.thread_id].finished
        assert not store.progress  # revoked execution must not overwrite cancellation
        manager.admit("owner")
        await manager.close()

    with caplog.at_level(logging.WARNING):
        asyncio.run(go())
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


@pytest.mark.parametrize("kind", ["brief", "bom"])
def test_revocation_during_bom_save_is_not_logged_as_validation_failure(kind, monkeypatch, caplog):
    from continuity.api import bom
    from continuity.api.execution import _run_bom
    from continuity.api.store import RunLeaseLost

    class RevokedStore(RecordingStore):
        async def save_bom(self, *args):
            raise RunLeaseLost("Run lease was lost")

        async def save_run_events(self, *args):
            raise RunLeaseLost("Run lease was lost")

        async def save_progress(self, *args):
            raise RunLeaseLost("Run lease was lost")

    async def go():
        store = RevokedStore()
        stream = EventStream(f"revoked-save-{kind}")
        app_module.STREAMS[stream.thread_id] = stream

        async def validate_bom(*args):
            yield stream.bom([], 0)

        monkeypatch.setattr(bom, "validate_bom", validate_bom)
        producer = _run(BomGraph(), stream.thread_id, {}, store) if kind == "brief" else _run_bom([], None, stream, store)
        with pytest.raises(RunLeaseLost):
            async for _ in producer:
                pass
        assert not store.progress

    with caplog.at_level(logging.WARNING):
        asyncio.run(go())
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
