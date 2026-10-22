"""Run execution outlives HTTP subscriptions; persisted events support reconnects."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from fastapi import HTTPException

from . import events
from .store import Store, execution_lease
from ..designs.context import current, ExecutionContext

log = logging.getLogger(__name__)


@dataclass
class Run:
    owner: str
    frames: list[dict] = field(default_factory=list)
    task: asyncio.Task | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    finished: bool = False
    stopping: bool = False
    cursor: int = -1
    touched: float = field(default_factory=time.monotonic)


class RunManager:
    def __init__(self, capacity: int = 4, timeout: int = 600):
        self.capacity = capacity
        self.timeout = timeout
        self.runs: OrderedDict[str, Run] = OrderedDict()
        self.closing = False
        self.admission = asyncio.Lock()

    def admit(self, owner: str) -> None:
        active = [run for run in self.runs.values() if not run.finished]
        if self.closing or len(active) >= self.capacity:
            raise HTTPException(429, "The design service is busy. Try again shortly.", headers={"Retry-After": "10"})
        if any(run.owner == owner for run in active):
            raise HTTPException(409, "A design is already running. Reopen it or stop it first.")

    async def start(self, thread_id: str, owner: str, producer: AsyncIterator[str],
                    store: Store | None = None, cleanup: Callable[[], None] | None = None, last_seq: int = -1,
                    *, allow_cancelled: bool = False) -> None:
        async with self.admission:
            try:
                self.admit(owner)
                token = uuid.uuid4().hex
                if store and not await store.claim_run(thread_id, owner, token, allow_cancelled=allow_cancelled):
                    raise HTTPException(409, "This account already has an active run, or this run has finished.")
            except BaseException:
                await producer.aclose()
                raise
            run = Run(owner, cursor=last_seq)
            self.runs[thread_id] = run
            # Keep bounded recent history in local mode. PostgreSQL is authoritative online.
            for key, old in list(self.runs.items()):
                if old.finished and (len(self.runs) > 64 or time.monotonic() - old.touched > 900):
                    self.runs.pop(key, None)
            run.task = asyncio.create_task(self._drive(thread_id, run, producer, store, token, cleanup))
        # Let the task enter its cleanup scope before an immediate cancel can reach it.
        await asyncio.sleep(0)

    async def _drive(self, thread_id: str, run: Run, producer: AsyncIterator[str],
                     store: Store | None, token: str, cleanup: Callable[[], None] | None) -> None:
        context = execution_lease.set((thread_id, token) if store else None)
        journal = current.set(ExecutionContext(thread_id, store))
        outcome = "abandoned"
        failure = False
        terminal_event: dict | None = None

        async def renew() -> None:
            while True:
                await asyncio.sleep(10)
                if store and not await store.renew_run(thread_id, token):
                    raise RuntimeError("Execution lease expired")

        async def consume() -> None:
            nonlocal terminal_event
            async for frame in producer:
                for line in frame.splitlines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    if store:
                        await store.append_stream_event(thread_id, token, event)
                    if len(run.frames) >= 10000:
                        raise RuntimeError("Run event limit exceeded")
                    run.cursor = event["seq"]
                    if event["type"] in {"done", "question", "error"}:
                        terminal_event = event
                    else:
                        run.frames.append(event)
                        run.changed.set()

        worker = asyncio.create_task(consume())
        lease = asyncio.create_task(renew())
        try:
            async with asyncio.timeout(self.timeout):
                done, _ = await asyncio.wait((worker, lease), return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    await task
        except asyncio.CancelledError:
            raise
        except Exception:
            failure = True
            log.exception("Run failed: %s", thread_id)
        finally:
            worker.cancel()
            lease.cancel()
            await asyncio.gather(worker, lease, return_exceptions=True)
            try:
                await producer.aclose()
            except Exception:
                failure = True
                log.exception("Could not close run producer: %s", thread_id)
            if failure:
                event = {"type": "error", "thread_id": thread_id, "seq": run.cursor + 1,
                         "message": "The run stopped. Reopen the project to recover its saved checkpoint.",
                         "recoverable": store is not None}
                terminal_event = event
                if store:
                    try:
                        await store.append_stream_event(thread_id, token, event)
                    except Exception:
                        log.exception("Could not persist run failure: %s", thread_id)
            if terminal_event:
                outcome = {"done": "done", "question": "awaiting", "error": "error"}[terminal_event["type"]]
            elif run.frames:
                outcome = {"done": "done", "question": "awaiting", "error": "error"}.get(
                    run.frames[-1]["type"], "abandoned")
            if store:
                try:
                    await store.release_run(thread_id, token, outcome)
                except Exception:
                    log.exception("Could not release run lease: %s", thread_id)
            execution_lease.reset(context)
            current.reset(journal)
            # A visible question must already be answerable; a visible completion must
            # already have its summary and BOM saved. Publish after producer persistence
            # and lease release, rather than racing a fast answer or navigation.
            if terminal_event:
                run.frames.append(terminal_event)
            run.finished = True
            run.touched = time.monotonic()
            run.changed.set()
            if cleanup:
                cleanup()

    async def subscribe(self, thread_id: str, owner: str, after: int = -1,
                        store: Store | None = None) -> AsyncIterator[str]:
        run = self.runs.get(thread_id)
        if run and run.owner == owner:
            while True:
                run.changed.clear()
                for event in run.frames:
                    if event["seq"] > after:
                        after = event["seq"]
                        yield events.frame(event)
                if run.finished:
                    return
                try:
                    await asyncio.wait_for(run.changed.wait(), timeout=10)
                except asyncio.TimeoutError:
                    yield events.HEARTBEAT
        elif store:
            while True:
                batch = await store.events_after(thread_id, owner, after)
                for event in batch:
                    after = event["seq"]
                    yield events.frame(event)
                if len(batch) == 500:
                    continue
                await store.recover_stale_runs()
                thread = await store.thread_for_user(thread_id, owner)
                if thread is None or thread.status != "running":
                    return
                yield events.HEARTBEAT
                await asyncio.sleep(1)

    async def stop(self, thread_id: str, owner: str) -> bool:
        run = self.runs.get(thread_id)
        if not run or run.owner != owner:
            return False
        # Repeated clicks, requests, and shutdown must join the same cleanup. A second
        # Task.cancel() can interrupt its finally block and leave admission occupied.
        if not run.finished and not run.stopping:
            run.stopping = True
            run.task.cancel()
        if run.task:
            await asyncio.shield(asyncio.gather(run.task, return_exceptions=True))
        return True

    async def close(self) -> None:
        self.closing = True
        await asyncio.gather(*(self.stop(key, run.owner) for key, run in self.runs.items()),
                             return_exceptions=True)
