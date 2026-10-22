"""Graph and BOM event producers. HTTP subscription lifetime is managed separately."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Sequence

from ..engine.models import PartSpec
from .. import ai_activity
from ..graph import nodes
from ..parts import dossier, normalize
from . import bom, events
from .memory import FindingRecorder
from .store import Store, RunLeaseLost
from .state import BOMS, FINDING_RECORDERS, PROGRESS_TASKS, STREAMS

log = logging.getLogger(__name__)

async def _run(
    graph: Any,
    thread_id: str,
    payload: Any,
    store: Store | None = None,
    user_id: str | None = None,
) -> AsyncIterator[str]:
    """Produce graph events and persist its final board; RunManager owns its lifetime."""
    stream = STREAMS[thread_id]
    configurable: dict[str, Any] = {"thread_id": thread_id, "events": stream}
    if store is not None and user_id is not None:
        async def precedent_lookup(signature: str) -> list[dict[str, Any]]:
            return await store.precedents_for_user(
                user_id, signature, exclude_thread=thread_id
            )

        configurable["precedent_lookup"] = precedent_lookup
    config = {"configurable": configurable}
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    outcome = "running"
    summary: dict[str, Any] | None = None
    placed_parts: dict[str, PartSpec] = {}
    recorder = FINDING_RECORDERS.setdefault(thread_id, FindingRecorder()) if store is not None else None
    trace: list[dict[str, Any]] = []

    async def dossier_lookup(mpn: str) -> list[dict[str, Any]]:
        if store is None:
            return []
        return (await store.part_facts([mpn])).get(mpn, [])

    lookup_token = normalize.set_dossier_lookup(dossier_lookup if store is not None else None)

    def ai_receipt(receipt: dict) -> None:
        text, level = ai_activity.message(receipt)
        event = stream.reasoning(None, text, level=level)
        event["research" if receipt["provider"] == "exa" else "ai"] = receipt
        nodes._emit(event)

    async def pump_events() -> None:
        nonlocal outcome, summary
        try:
            async for mode, chunk in graph.astream(
                payload, config, stream_mode=["updates", "custom"]
            ):
                if mode == "custom":
                    if recorder is not None:
                        recorder.feed(chunk)
                    if chunk.get("type") == "bom":
                        BOMS[thread_id] = chunk["rows"]
                        if store is not None:
                            await store.save_bom(thread_id, chunk["rows"])
                            if placed_parts:
                                await store.save_part_facts(
                                    fact
                                    for part in placed_parts.values()
                                    for fact in dossier.facts_from_part(part)
                                )
                    if chunk.get("type") == "done":
                        outcome = "done"
                        summary = chunk.get("summary")
                    if chunk.get("type") != "bom":
                        trace.append(chunk)
                    await queue.put(events.frame(chunk))
                    continue

                _remember_placed_parts(chunk, placed_parts)

                # `__interrupt__` is a *top-level* key of the chunk, alongside node
                # names — not nested inside a node's update. Looking one level down
                # finds nothing, and the stream stops with no question on screen.
                for event in _interrupt_events(stream, chunk):
                    outcome = "awaiting"
                    trace.append(event)
                    await queue.put(events.frame(event))
        except asyncio.CancelledError:
            outcome = "abandoned"
            raise
        except RunLeaseLost:
            outcome = "abandoned"
            raise
        except Exception:  # expose a safe message; keep diagnostics in server logs
            outcome = "error"
            log.exception("Graph execution failed: %s", thread_id)
            event = stream.error("The design could not finish. Reopen the project to recover or start a new design.", recoverable=store is not None)
            trace.append(event)
            await queue.put(events.frame(event))
        finally:
            if store is not None:
                async def persist() -> None:
                    await _save_trace(store, thread_id, trace)
                    await store.save_progress(thread_id, stream.last_seq, outcome)
                    if summary is not None:
                        await store.save_summary(thread_id, summary)
                        await store.save_findings(thread_id, recorder.findings())

                progress_task = asyncio.create_task(persist())
                PROGRESS_TASKS.add(progress_task)
                progress_task.add_done_callback(PROGRESS_TASKS.discard)
                try:
                    await asyncio.shield(progress_task)
                except RunLeaseLost:
                    log.info("Skipped final writes for revoked run: %s", thread_id)
                if outcome == "done":
                    FINDING_RECORDERS.pop(thread_id, None)
            await queue.put(None)

    async def pump() -> None:
        # Keep the observer's ContextVar token inside this task, even on cancellation.
        # Graph tasks inherit it; their receipts use the same ordered stream writer.
        with ai_activity.observe(ai_receipt):
            await pump_events()

    task = asyncio.create_task(pump())
    task.add_done_callback(lambda _: queue.put_nowait(None))
    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(), timeout=events.HEARTBEAT_INTERVAL_S
                )
            except asyncio.TimeoutError:
                yield events.HEARTBEAT
                continue
            if item is None:
                await task
                return
            yield item
    finally:
        # Closing the producer (explicit cancellation or shutdown) stops its graph.
        if not task.done():
            task.cancel()
            nodes.clear_prefetches(thread_id, cancel=True)
        elif outcome != "awaiting":
            nodes.clear_prefetches(thread_id, cancel=True)
        await asyncio.gather(task, return_exceptions=True)
        normalize.reset_dossier_lookup(lookup_token)



async def _with_heartbeats(
    events_in: AsyncIterator[dict[str, Any]],
) -> AsyncIterator[dict[str, Any] | None]:
    """Yield each event, and `None` whenever the producer has been quiet too long.

    A plain `async for` over a slow generator sends nothing while it works. This turns
    the gap into a heartbeat the client can count on, without the producer having to
    know it is being streamed.
    """
    pending: asyncio.Task[dict[str, Any]] | None = None
    iterator = events_in.__aiter__()
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(anext(iterator))
            try:
                yield await asyncio.wait_for(
                    asyncio.shield(pending), timeout=events.HEARTBEAT_INTERVAL_S
                )
            except asyncio.TimeoutError:
                yield None
                continue
            except StopAsyncIteration:
                return
            pending = None
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def _run_bom(
    rows: list[bom.BomRow], brief: str | None, stream: events.EventStream, store: Store | None
) -> AsyncIterator[str]:
    """Frame the independent BOM-validation path without touching graph `_run`."""
    outcome = "running"
    summary: dict[str, Any] | None = None
    recorder = FINDING_RECORDERS.setdefault(stream.thread_id, FindingRecorder()) if store is not None else None
    placed_parts: list[PartSpec] = []
    trace: list[dict[str, Any]] = []

    def remember(parts: Sequence[PartSpec]) -> None:
        placed_parts[:] = parts

    async def dossier_lookup(mpn: str) -> list[dict[str, Any]]:
        if store is None:
            return []
        return (await store.part_facts([mpn])).get(mpn, [])

    observer_token = bom.set_placed_parts_observer(remember)
    lookup_token = normalize.set_dossier_lookup(dossier_lookup if store is not None else None)
    try:
        # Heartbeats, for the same reason `_run` has them — and this path needs them
        # *more*. It resolves every MPN before it can emit anything, so the stream is
        # genuinely silent for twenty seconds or more on a real BOM, and the client
        # treats thirty seconds without a byte as a dead connection. Without this the
        # first useful run of a five-line BOM died with "the connection went quiet".
        async for event in _with_heartbeats(bom.validate_bom(rows, stream, brief)):
            if event is None:
                yield events.HEARTBEAT
                continue
            if recorder is not None:
                recorder.feed(event)
            if event["type"] == "bom":
                BOMS[stream.thread_id] = event["rows"]
                if store is not None:
                    await store.save_bom(stream.thread_id, event["rows"])
                    if placed_parts:
                        await store.save_part_facts(
                            fact
                            for part in placed_parts
                            for fact in dossier.facts_from_part(part)
                        )
            if event["type"] == "done":
                outcome = "done"
                summary = event["summary"]
            if event["type"] != "bom":
                trace.append(event)
            yield events.frame(event)
    except (asyncio.CancelledError, GeneratorExit, RunLeaseLost):
        # A stopped producer has not completed validation. Persist from a shielded
        # task below; a revoked lease prevents it from overwriting cancellation.
        outcome = "abandoned"
        raise
    except Exception:  # this stream remains useful even if a dependency breaks
        outcome = "error"
        log.exception("BOM validation failed: %s", stream.thread_id)
        event = stream.error("The BOM could not be validated. Check its part numbers and try again.", recoverable=False)
        trace.append(event)
        yield events.frame(event)
    finally:
        try:
            if store is not None:
                async def persist() -> None:
                    await _save_trace(store, stream.thread_id, trace)
                    await store.save_progress(stream.thread_id, stream.last_seq, outcome)
                    if summary is not None:
                        await store.save_summary(stream.thread_id, summary)
                        await store.save_findings(stream.thread_id, recorder.findings())

                progress_task = asyncio.create_task(persist())
                PROGRESS_TASKS.add(progress_task)
                progress_task.add_done_callback(PROGRESS_TASKS.discard)
                try:
                    await asyncio.shield(progress_task)
                except RunLeaseLost:
                    log.info("Skipped final writes for revoked run: %s", stream.thread_id)
                if outcome == "done":
                    FINDING_RECORDERS.pop(stream.thread_id, None)
        finally:
            bom.reset_placed_parts_observer(observer_token)
            normalize.reset_dossier_lookup(lookup_token)


def _remember_placed_parts(update: Any, placed_parts: dict[str, PartSpec]) -> None:
    """Keep the `PartSpec`s already delivered by graph updates until their BOM is saved."""
    if not isinstance(update, dict):
        return
    for value in update.values():
        slots = value.get("slots") if isinstance(value, dict) else None
        if not isinstance(slots, dict):
            continue
        for slot_id, slot in slots.items():
            part = getattr(slot, "part", None)
            if isinstance(slot_id, str) and isinstance(part, PartSpec):
                placed_parts[slot_id] = part
            elif isinstance(slot_id, str):
                placed_parts.pop(slot_id, None)


def _interrupt_frames(stream: events.EventStream, value: Any) -> list[str]:
    """Turn LangGraph's `__interrupt__` payload into a contract `question` frame."""
    return [events.frame(event) for event in _interrupt_events(stream, value)]


def _interrupt_events(stream: events.EventStream, value: Any) -> list[dict[str, Any]]:
    """Turn LangGraph's `__interrupt__` payload into contract-shaped question events."""
    payloads = _interrupt_payloads(value)
    return [
        stream.question(
            payload.get("question_id", "q"),
            payload.get("text", ""),
            payload.get("suggestions", []),
            alternatives=payload.get("alternatives"),
        )
        for payload in payloads
    ]


def _interrupt_payloads(value: Any) -> list[dict[str, Any]]:
    """Normalise the payload LangGraph gives both stream updates and state tasks."""
    if not isinstance(value, dict):
        return []
    interrupts = value.get("__interrupt__")
    if not interrupts:
        return []

    payloads = []
    for item in interrupts:
        payload = getattr(item, "value", item)
        if not isinstance(payload, dict):
            payload = {"question_id": "q", "text": str(payload), "suggestions": []}
        payloads.append(payload)
    return payloads


def _pending_question(snapshot: Any, thread: Any) -> dict[str, Any] | None:
    """Expose the exact payload retained by LangGraph's pending interrupt task."""
    if snapshot is None:
        return None
    for task in getattr(snapshot, "tasks", ()):
        payloads = _interrupt_payloads({"__interrupt__": getattr(task, "interrupts", ())})
        if payloads:
            payload = payloads[0]
            return {
                "type": "question",
                "seq": thread.last_seq,
                "thread_id": thread.id,
                "question_id": payload.get("question_id", "q"),
                "text": payload.get("text", ""),
                "suggestions": list(payload.get("suggestions", [])),
                **({"alternatives": list(payload["alternatives"])} if payload.get("alternatives") else {}),
            }
    return None


async def _save_trace(store: Store, thread_id: str, trace: Sequence[dict[str, Any]]) -> None:
    """Best-effort history: its failure must never make a live board fail."""
    # The run manager commits canonical boards and their revision atomically. The
    # producer must not race that transaction with an unsanitized duplicate frame.
    trace = [event for event in trace if not (event.get("type") == "board" and "document" in event)]
    if not trace:
        return
    try:
        await store.save_run_events(thread_id, trace)
    except RunLeaseLost:
        raise
    except Exception:
        log.warning("could not persist trace for thread %s", thread_id, exc_info=True)
