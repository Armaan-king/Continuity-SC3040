"""FastAPI + SSE. Contract §1.

The frontend's only integration point. `useDesignSession.ts` swaps one line to point
at this, so everything here exists to make the wire behave exactly as the mock did.

## Why the event stream lives per thread and not in graph state

`seq` must never repeat, and LangGraph re-executes nodes on resume and on retry. A
counter kept in checkpointed state would rewind with the checkpoint and re-issue
numbers the client has already seen — which the client would then silently drop,
because it discards anything at or below its high-water mark.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import csv
import io
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Sequence

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field, ValidationError

from .. import env
from ..engine.models import PartSpec, Slot
from ..graph import nodes
from ..graph.build import build
from ..planner import topology
from ..parts import datasheet, dossier, normalize
from . import analytics_api, auth, bom, events, memory, projects
from .memory import FindingRecorder
from .store import Store

env.load()

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the pool, the checkpointer and the store — or run without any of them.

    `DATABASE_URL` unset means **single-user local mode**: an in-memory checkpointer, no
    store, no accounts. That is what the 285-test offline suite drives, and keeping it
    working is the reason this branch exists rather than a hard requirement on Postgres.

    The failure mode to be aware of: an app deployed without `DATABASE_URL` runs open —
    no accounts, and every restart loses every thread. It is announced at startup rather
    than reported by `/health`, because the contract fixes that response to
    `{"status":"ok"}` and this is an operator's concern, not the frontend's.
    """
    url = os.environ.get("DATABASE_URL")

    if not url:
        log.warning(
            "DATABASE_URL is not set — running single-user and in-memory. "
            "No accounts, and every thread is lost on restart."
        )
        yield
        return

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    # `check` matters against a database that suspends. A serverless Postgres — Neon's
    # free tier, for one — closes every session after a few minutes of inactivity, and it
    # cannot be told not to. The pool keeps its connections open across that, so without a
    # check the first request after any quiet spell is handed a socket the server has
    # already closed and fails, once, for no reason the user can see. `check_connection`
    # tests a connection on the way out of the pool and quietly replaces a dead one.
    #
    # Use the *direct* connection string rather than the `-pooler` one: this pool is
    # already a client-side pool, and both `checkpointer.setup()` and `store.setup()` run
    # DDL at startup, which transaction-mode poolers are not the place for.
    pool = AsyncConnectionPool(
        url,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"autocommit": True},
        check=AsyncConnectionPool.check_connection,
    )
    await pool.open()

    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()

    store = Store(pool)
    await store.setup()

    app.state.pool = pool
    app.state.store = store
    app.state.graph = build(checkpointer)
    log.info("persistence: postgres")
    try:
        yield
    finally:
        await pool.close()


app = FastAPI(title="Continuity", version="0.1.0", lifespan=lifespan)

# The defaults, so the app is usable the moment it is imported. `httpx.ASGITransport`
# does not run lifespan events, which is how the offline suite drives this app — without
# these it would reach for `app.state.graph` and find nothing. The lifespan *replaces*
# them when `DATABASE_URL` is set; it never has to create them.
app.state.pool = None
app.state.store = None
app.state.graph = build(InMemorySaver())

ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CONTINUITY_ORIGINS", "http://localhost:5173,http://localhost:5174"
    ).split(",")
    if origin.strip()
]
"""Where the UI is served from. Set `CONTINUITY_ORIGINS` on any deployed instance.

This used to be `["*"]`, which is not a tightening that could be left until deploy: a
wildcard origin is **rejected outright** by browsers when credentials are allowed, and
the session is a cookie. Naming the origins is what makes signing in work at all, so the
two changes had to land together.
"""

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_credentials=True,
    # Explicit for the same reason as the origins — wildcards and credentials do not mix.
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
)

app.include_router(auth.router)
app.include_router(memory.router)
app.include_router(analytics_api.router)
app.include_router(projects.router)

STREAMS: dict[str, events.EventStream] = {}
"""thread_id → the live counter for a run in flight.

Necessarily per-process: an `EventStream` belongs to a connection being written to right
now. What survives a restart is not this dict but `threads.last_seq`, which `/resume`
reads to rebuild a stream that carries on numbering where the last one stopped.
"""

BOMS: dict[str, list[dict[str, Any]]] = {}
"""thread_id → the last BOM emitted, so `/export` does not have to replay the graph.

A cache in front of `threads.bom`, and only that. With no store configured it is the
whole of the record; with one, the database is authoritative and this saves a read.
"""

FINDING_RECORDERS: dict[str, FindingRecorder] = {}
"""Live, API-only event correlations. They are never provided to the graph."""

PROGRESS_TASKS: set[asyncio.Task[None]] = set()
"""Terminal writes still running after the client has disconnected."""

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # nginx and friends will otherwise buffer the whole stream
}


class DesignRequest(BaseModel):
    prompt: str
    project_id: str | None = None
    """Optional project for the run; signed-in users otherwise use their scratch project.

    Ignored in single-user local mode, where there are no projects to belong to.
    """


class ResumeRequest(BaseModel):
    thread_id: str
    answer: str


class DatasheetRequest(BaseModel):
    mpn: str = Field(max_length=256)
    package: str = Field(max_length=256)
    document: str = Field(max_length=((datasheet.MAX_PDF_BYTES + 2) // 3) * 4)


MAX_DATASHEET_REQUEST_BYTES = ((datasheet.MAX_PDF_BYTES + 2) // 3) * 4 + 1024
"""Largest valid base64 PDF plus the bounded JSON fields around it."""


@app.post("/bom/validate")
async def validate_pasted_bom(body: bom.BomRequest, request: Request) -> StreamingResponse:
    """Validate an existing BOM on its own path; it never enters the planner graph."""
    try:
        rows = bom.parse_bom(body.bom)
    except bom.BomInputError as error:
        raise HTTPException(422, str(error)) from error

    store = request.app.state.store
    user = await _signed_in(request)
    thread_id = uuid.uuid4().hex[:12]
    if store is not None:
        project_id = body.project_id
        if project_id is None:
            project_id = await store.ensure_scratch_project(user.id)
        elif not project_id:
            raise HTTPException(422, "project_id is required")
        if await store.project_for_user(project_id, user.id) is None:
            raise HTTPException(404, "no such project")
        await store.create_thread(thread_id, project_id, user.id, "BOM validation")

    stream = STREAMS[thread_id] = events.EventStream(thread_id)
    return StreamingResponse(
        _run_bom(rows, body.prompt, stream, store),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _signed_in(request: Request) -> Any:
    """The user, or `None` when this instance has no accounts at all.

    Single-user local mode is the only way this returns `None`: with a store configured,
    a missing or expired cookie is a 401 rather than an anonymous run.
    """
    if request.app.state.store is None:
        return None
    return await auth.current_user(request)


async def _datasheet_body(request: Request) -> DatasheetRequest:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_DATASHEET_REQUEST_BYTES:
                raise HTTPException(422, "document exceeds the PDF size limit")
        except ValueError as error:
            raise HTTPException(422, "content-length must be an integer") from error

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_DATASHEET_REQUEST_BYTES:
            raise HTTPException(422, "document exceeds the PDF size limit")
    try:
        return DatasheetRequest.model_validate_json(body)
    except ValidationError as error:
        raise HTTPException(422, "malformed datasheet request") from error


@app.post("/datasheet")
async def extract_datasheet_theta_ja(request: Request) -> dict[str, str | float | None]:
    """Extract and cache one evidence-backed θJA fact from a browser-supplied PDF."""
    await _signed_in(request)
    body = await _datasheet_body(request)
    mpn = body.mpn.strip()
    package = body.package.strip()
    document = body.document.strip()
    if not mpn:
        raise HTTPException(422, "mpn is required")
    if not package:
        raise HTTPException(422, "package is required")
    if not document:
        raise HTTPException(422, "document must not be empty")
    max_encoded_bytes = ((datasheet.MAX_PDF_BYTES + 2) // 3) * 4
    if len(document) > max_encoded_bytes:
        raise HTTPException(422, "document exceeds the PDF size limit")
    try:
        data = base64.b64decode(document, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(422, "document must be valid base64") from error
    if not data:
        raise HTTPException(422, "document must not be empty")
    if len(data) > datasheet.MAX_PDF_BYTES:
        raise HTTPException(422, "document exceeds the PDF size limit")

    text = datasheet.text_from_pdf(data)
    if text is None:
        return {
            "mpn": mpn,
            "theta_ja": None,
            "source_line": None,
            "reason": "The document could not be read as a PDF.",
        }
    fact = await datasheet.theta_ja_from_text(text, mpn=mpn, package=package)
    if fact is None:
        return {
            "mpn": mpn,
            "theta_ja": None,
            "source_line": None,
            "reason": "No usable θJA was found in this datasheet.",
        }
    return {
        "mpn": mpn,
        "theta_ja": fact.theta_ja,
        "source_line": fact.source_line,
        "reason": None,
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/design")
async def design(body: DesignRequest, request: Request) -> StreamingResponse:
    store = request.app.state.store
    user = await _signed_in(request)
    thread_id = uuid.uuid4().hex[:12]

    if store is not None:
        project_id = body.project_id
        if project_id is None:
            project_id = await store.ensure_scratch_project(user.id)
        elif not project_id:
            raise HTTPException(422, "project_id is required")
        if await store.project_for_user(project_id, user.id) is None:
            raise HTTPException(404, "no such project")
        await store.create_thread(thread_id, project_id, user.id, body.prompt)

    STREAMS[thread_id] = events.EventStream(thread_id)
    return StreamingResponse(
        _run(request.app.state.graph, thread_id, {"prompt": body.prompt}, store, user.id if user else None),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.post("/resume")
async def resume(body: ResumeRequest, request: Request) -> StreamingResponse:
    """Continue a paused run — including one paused before this process started.

    `STREAMS` holds the counter for a run in flight and nothing more. When it has no
    entry the run is not necessarily unknown; the process may simply be a different one
    than the one that paused it. The stream is then rebuilt from the persisted
    `last_seq`, because a counter that restarts at 0 is discarded by the client in
    silence rather than reported.
    """
    store = request.app.state.store
    user = await _signed_in(request)
    stream = STREAMS.get(body.thread_id)

    if store is not None:
        thread = await store.thread_for_user(body.thread_id, user.id)
        if thread is None:
            raise HTTPException(404, "unknown thread")
        if stream is None:
            stream = events.EventStream(body.thread_id, last_seq=thread.last_seq)
            STREAMS[body.thread_id] = stream
    elif stream is None:
        raise HTTPException(404, "unknown thread")

    return StreamingResponse(
        _run(
            request.app.state.graph,
            body.thread_id,
            Command(resume=body.answer),
            store,
            user.id if user else None,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.post("/threads/{thread_id}/continue")
async def continue_thread(thread_id: str, request: Request) -> StreamingResponse:
    """Continue a disconnected or failed graph from its last completed checkpoint."""
    store = request.app.state.store
    user = await _signed_in(request)
    if store is None:
        raise HTTPException(404, "unknown thread")

    thread = await store.thread_for_user(thread_id, user.id)
    if thread is None:
        raise HTTPException(404, "unknown thread")
    if thread.status not in {"abandoned", "error"}:
        raise HTTPException(409, "only stopped runs can be continued")

    # A continuation is a fresh HTTP stream, even if the old process left an in-memory
    # EventStream behind. Start its counter from the durable high-water mark so every
    # newly emitted frame survives the client's sequence gate.
    STREAMS[thread_id] = events.EventStream(thread_id, last_seq=thread.last_seq)
    await store.save_progress(thread_id, thread.last_seq, "running")
    return StreamingResponse(
        _run(request.app.state.graph, thread_id, None, store, user.id),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.get("/threads/{thread_id}/board")
async def thread_board(thread_id: str, request: Request) -> dict[str, Any]:
    """Return a finished board without replaying the work that produced it."""
    store = request.app.state.store
    user = await _signed_in(request)

    if store is None:
        raise HTTPException(404, "unknown thread")

    thread = await store.thread_for_user(thread_id, user.id)
    if thread is None:
        raise HTTPException(404, "unknown thread")

    if thread.status == "running":
        return {
            "status": thread.status,
            "summary": thread.summary,
            "slots": [],
            "edges": [],
            "bom": None,
            "checkpoint": "not_loaded",
            "trace": [],
            "question": None,
            "resumable": False,
        }

    snapshot = await _checkpoint_snapshot(request.app.state.graph, thread_id)
    values = snapshot.values if snapshot and isinstance(snapshot.values, dict) else None
    trace = await store.run_events(thread_id)
    question = _pending_question(snapshot, thread)
    resumable = thread.status in {"awaiting", "abandoned", "error"}
    if values is None:
        return _bom_only_board(thread, trace, question, resumable)

    slots = values.get("slots")
    rails = values.get("rails")
    requirements = values.get("requirements")
    if not isinstance(slots, dict) or not slots or not isinstance(rails, dict) or requirements is None:
        # Read fine, no board in it. A run paused at `clarify` has not planned one yet, and
        # the question it is waiting on is the whole point of restoring it.
        return _bom_only_board(thread, trace, question, resumable, checkpoint="not_planned")

    try:
        board = topology.Board(requirements, slots, rails)
        edges = topology.resolved_edges(board, values.get("verdicts") or [])
        # The board input travels with the restored board for the same reason it travels
        # on `plan`: without it the client has no node for a power edge to start from, and
        # drops every edge out of the supply. A reopened project would then show the
        # regulator floating again — the bug fixed, but only until you closed the tab.
        supply = topology.supply_node(topology.power_source(requirements))
    except Exception:
        log.warning("could not restore graph edges for thread %s", thread_id, exc_info=True)
        return _bom_only_board(thread, trace, question, resumable)

    restored_slots = [_slot(slot) for slot in slots.values() if isinstance(slot, Slot)]
    if len(restored_slots) != len(slots):
        return _bom_only_board(thread, trace, question, resumable)

    rows = [events.bom_row(slot_id, slot.part) for slot_id, slot in slots.items() if slot.part]
    return {
        "status": thread.status,
        "summary": thread.summary,
        "slots": restored_slots,
        "edges": [events._edge(edge) for edge in edges],
        "supply": supply,
        "bom": _board_bom(rows),
        "checkpoint": "available",
        "trace": trace,
        "question": question,
        "resumable": resumable,
    }


async def _checkpoint_snapshot(graph: Any, thread_id: str) -> Any | None:
    """Read a checkpoint snapshot, treating an old or missing one as recoverable."""
    try:
        return await graph.aget_state({"configurable": {"thread_id": thread_id}})
    except Exception:
        log.warning("could not restore checkpoint for thread %s", thread_id, exc_info=True)
        return None


async def _checkpoint_values(graph: Any, thread_id: str) -> dict[str, Any] | None:
    """The state values retained for callers that only need the board itself."""
    snapshot = await _checkpoint_snapshot(graph, thread_id)
    return snapshot.values if snapshot and isinstance(snapshot.values, dict) else None


def _slot(slot: Slot) -> dict[str, Any]:
    return {
        "id": slot.id,
        "label": slot.label,
        "tier": slot.tier,
        "pinned": slot.pinned,
        "status": slot.status,
        "part": events._part(slot.part) if slot.part is not None else None,
        "constraint": dict(slot.constraint) if slot.constraint is not None else None,
        "repair_count": slot.repair_count,
    }


def _board_bom(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum((row["unit_price"] or 0) * row["qty"] for row in rows)
    return {
        "rows": rows,
        "total": round(total, 2),
        "currency": rows[0]["currency"] if rows else "USD",
    }


def _bom_only_board(
    thread: Any,
    trace: list[dict[str, Any]] | None = None,
    question: dict[str, Any] | None = None,
    resumable: bool = False,
    checkpoint: str = "unavailable",
) -> dict[str, Any]:
    """A board that could not be restored, and why — the reason is not decoration.

    `unavailable` means the checkpoint could not be read and the board is genuinely lost.
    `not_planned` means it was read perfectly and holds no board *yet*, which is the
    ordinary state of a run paused at `clarify` before planning has happened. Collapsing
    the two hid a real failure behind a routine one: a run interrupted on its first
    question reported its board as lost, and a genuinely lost board looked equally normal.
    """
    rows = thread.bom if thread.bom is not None else BOMS.get(thread.id, [])
    return {
        "status": thread.status,
        "summary": thread.summary,
        "slots": [],
        "edges": [],
        "bom": _board_bom(rows),
        "checkpoint": checkpoint,
        "trace": trace or [],
        "question": question,
        "resumable": resumable,
    }


@app.post("/design/demo")
async def walkthrough(request: Request) -> StreamingResponse:
    """The first run a new account sees: a stored one, replayed.

    Deterministic on purpose. This is the first thing anybody encounters, and a live run
    here would put the distributor and the model on the critical path of a first
    impression. The frames are a capture of a real run against real part data, renumbered
    onto a fresh thread, and the UI's own pacing is what makes it read as work happening.

    **Idempotent per account**, and it has to be. React re-runs effects in development, a
    refresh mid-tour would call it again, and two requests arriving together both read
    `onboarded_at IS NULL` before either commits. When that was a 409-or-create, a new
    account reliably got *two* "Welcome to Continuity" projects, the first abandoned
    mid-stream and left showing RUNNING for ever.

    So it looks for the walkthrough this account already has and replays into that thread.
    Nothing is rewritten by a second call: the frames are a fixed recording and the thread
    row is the same one.
    """
    store = auth.store_of(request)
    user = await auth.current_user(request)

    thread_id = await store.ensure_walkthrough(user.id, walkthrough_prompt())
    await store.mark_onboarded(user.id)

    return StreamingResponse(
        _replay(thread_id, store), media_type="text/event-stream", headers=SSE_HEADERS
    )


@app.get("/export/{thread_id}.csv")
async def export(thread_id: str, request: Request) -> PlainTextResponse:
    store = request.app.state.store
    user = await _signed_in(request)

    if store is None:
        rows = BOMS.get(thread_id)
    else:
        thread = await store.thread_for_user(thread_id, user.id)
        rows = None if thread is None else (thread.bom or BOMS.get(thread_id))

    if rows is None:
        raise HTTPException(404, "no bill of materials for that thread")

    buffer = io.StringIO()
    columns = [
        "slot", "mpn", "manufacturer", "description", "qty",
        "unit_price", "currency", "stock", "distributor", "lead_time_days",
        "datasheet", "product_url",
    ]
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)

    return PlainTextResponse(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="continuity-{thread_id}.csv"'},
    )



WALKTHROUGH = Path(__file__).with_name("walkthrough.jsonl")
WALKTHROUGH_NAME = "Welcome to Continuity"
"""The project the walkthrough leaves behind, and the name it appears under."""


def walkthrough_frames() -> list[dict[str, Any]]:
    """The recorded run. Read from disk each time — it is a few KB and never hot."""
    if not WALKTHROUGH.exists():
        raise HTTPException(503, "no walkthrough has been recorded for this build")
    return [json.loads(line) for line in WALKTHROUGH.read_text().splitlines() if line.strip()]


def walkthrough_prompt() -> str:
    """The brief the recording was made from, so the thread row records what was asked."""
    for event in walkthrough_frames():
        if event.get("type") == "prompt":
            return event["text"]
    return "Recorded walkthrough"


async def _replay(thread_id: str, store: Store) -> AsyncIterator[str]:
    """Stream the recorded frames onto a fresh thread.

    Renumbering is not cosmetic. `seq` and `thread_id` are the two fields the client uses
    to decide what to keep and where to put it, so replaying a recording verbatim would
    hand it another thread's identity and a counter unrelated to this one.
    """
    # A fresh counter per replay: each call serves a new client whose high-water mark
    # starts below zero, and the frames are a fixed recording rather than a
    # continuation of anything.
    stream = STREAMS[thread_id] = events.EventStream(thread_id)
    rows: list[dict[str, Any]] | None = None
    recorded_summary: dict[str, Any] | None = None
    recorder = FindingRecorder()
    trace: list[dict[str, Any]] = []

    for event in walkthrough_frames():
        if event.get("type") == "prompt":
            continue
        stream.last_seq += 1
        frame = {**event, "seq": stream.last_seq, "thread_id": thread_id}
        if frame.get("type") == "bom":
            rows = frame["rows"]
        if frame.get("type") == "done":
            recorded_summary = frame.get("summary")
        if frame.get("type") != "bom":
            trace.append(frame)
        recorder.feed(frame)
        yield events.frame(frame)

    if rows is not None:
        BOMS[thread_id] = rows
        await store.save_bom(thread_id, rows)
    if recorded_summary is not None:
        await store.save_summary(thread_id, recorded_summary)
        await store.save_findings(thread_id, recorder.findings())
    await _save_trace(store, thread_id, trace)
    await store.save_progress(thread_id, stream.last_seq, "done")


# ── the stream ────────────────────────────────────────────────────────────────


async def _run(
    graph: Any,
    thread_id: str,
    payload: Any,
    store: Store | None = None,
    user_id: str | None = None,
) -> AsyncIterator[str]:
    """Drive the graph on a background task, framing what it emits.

    The graph runs into a queue rather than being iterated directly, so the heartbeat
    can fire *during* a node instead of only between them. That distinction did not
    matter while every node was sub-millisecond; now that a node searches a distributor
    and calls a model, a single placement can hold the connection quiet for longer than
    the client's 30-second death timer.

    ## What is written down, and when

    `last_seq` is persisted once, as the stream ends. That is the only moment it matters:
    a resume can only follow a stream that has stopped, and writing on every frame would
    put a round trip between the graph and the client for a number nobody reads until
    then. The status recorded alongside it is what the run actually did — `awaiting` for
    a question, `done` for a finished board, `error` for a failed run, or `abandoned`
    when its client disconnects.
    """
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
    cancelled = False
    placed_parts: dict[str, PartSpec] = {}
    recorder = FINDING_RECORDERS.setdefault(thread_id, FindingRecorder()) if store is not None else None
    trace: list[dict[str, Any]] = []

    async def dossier_lookup(mpn: str) -> list[dict[str, Any]]:
        if store is None:
            return []
        return (await store.part_facts([mpn])).get(mpn, [])

    lookup_token = normalize.set_dossier_lookup(dossier_lookup if store is not None else None)

    async def pump() -> None:
        nonlocal cancelled, outcome, summary
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
            cancelled = True
            outcome = "abandoned"
            raise
        except Exception as exc:  # a broken run must still tell the client why
            outcome = "error"
            event = stream.error(f"{type(exc).__name__}: {exc}", recoverable=False)
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
                if not cancelled:
                    await progress_task
                if outcome == "done":
                    FINDING_RECORDERS.pop(thread_id, None)
            await queue.put(None)

    task = asyncio.create_task(pump())
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
                return
            yield item
    finally:
        # A client that disconnects mid-run must not leave the graph running.
        if not task.done():
            task.cancel()
            nodes.clear_prefetches(thread_id, cancel=True)
        elif outcome != "awaiting":
            nodes.clear_prefetches(thread_id, cancel=True)
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


async def _run_bom(
    rows: list[bom.BomRow], brief: str | None, stream: events.EventStream, store: Store | None
) -> AsyncIterator[str]:
    """Frame the independent BOM-validation path without touching graph `_run`."""
    outcome = "running"
    summary: dict[str, Any] | None = None
    cancelled = False
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
    except asyncio.CancelledError:
        # Same shape as `_run`: a closed generator cannot complete an `await` in its
        # `finally`, so the one write recording the outcome is the write that cannot
        # happen. Record the abandonment and persist from a task that is not this one.
        cancelled = True
        outcome = "abandoned"
        raise
    except Exception as error:  # this stream remains useful even if a dependency breaks
        outcome = "error"
        event = stream.error(f"{type(error).__name__}: {error}", recoverable=False)
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
                if not cancelled:
                    await progress_task
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
            }
    return None


async def _save_trace(store: Store, thread_id: str, trace: Sequence[dict[str, Any]]) -> None:
    """Best-effort history: its failure must never make a live board fail."""
    if not trace:
        return
    try:
        await store.save_run_events(thread_id, trace)
    except Exception:
        log.warning("could not persist trace for thread %s", thread_id, exc_info=True)
