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
import hashlib
import io
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field, ValidationError

from .. import env, llm, logging_setup
from ..settings import Settings
from ..engine.models import Slot
from ..graph.build import build
from ..planner import topology
from ..parts import datasheet
from . import analytics_api, auth, bom, events, memory, projects, revisions
from .memory import FindingRecorder
from .store import Store
from .runs import RunManager
from .boards import recorded_board, revision_trace
from .security import SecurityMiddleware
from .state import BOMS, FINDING_RECORDERS, PROGRESS_TASKS, STREAMS
from .execution import _run, _run_bom, _save_trace, _pending_question, _interrupt_frames, _with_heartbeats

env.load()

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate settings and own database, graph, and run-manager resources."""
    logging_setup.configure()
    settings = Settings.read()
    llm.configuration()
    app.state.runs = RunManager(settings.max_active_runs, settings.run_timeout_seconds)
    url = settings.database_url

    if not url:
        log.warning(
            "DATABASE_URL is not set — running single-user and in-memory. "
            "No accounts, and every thread is lost on restart."
        )
        try:
            yield
        finally:
            await app.state.runs.close()
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
    await store.recover_stale_runs()
    await store.delete_expired_sessions()

    app.state.pool = pool
    app.state.store = store
    app.state.graph = build(checkpointer)
    log.info("persistence: postgres")
    from .recovery import recover
    recovery_task = asyncio.create_task(recover(app))
    try:
        yield
    finally:
        recovery_task.cancel()
        await asyncio.gather(recovery_task, return_exceptions=True)
        await app.state.runs.close()
        if PROGRESS_TASKS:
            await asyncio.gather(*PROGRESS_TASKS, return_exceptions=True)
        await pool.close()


app = FastAPI(title="Continuity", version="0.1.0", lifespan=lifespan)

# The defaults, so the app is usable the moment it is imported. `httpx.ASGITransport`
# does not run lifespan events, which is how the offline suite drives this app — without
# these it would reach for `app.state.graph` and find nothing. The lifespan *replaces*
# them when `DATABASE_URL` is set; it never has to create them.
app.state.pool = None
app.state.store = None
app.state.graph = build(InMemorySaver())
app.state.runs = RunManager()

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
    expose_headers=["X-Thread-ID"],
)

app.include_router(auth.router)
app.include_router(memory.router)
app.include_router(analytics_api.router)
app.include_router(projects.router)
app.include_router(revisions.router)
app.add_middleware(SecurityMiddleware)

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # nginx and friends will otherwise buffer the whole stream
}


class DesignRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    project_id: str | None = None
    request_id: str | None = Field(default=None, min_length=8, max_length=128)
    """Optional project for the run; signed-in users otherwise use their scratch project.

    Ignored in single-user local mode, where there are no projects to belong to.
    """


class ResumeRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128)
    answer: str = Field(min_length=1, max_length=2000, pattern=r"\S")


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
    owner = _owner(user)
    thread_id = _thread_id(owner, body.request_id, f"bom:{body.project_id}:{body.prompt}:{body.bom}")
    if (store and await store.thread_for_user(thread_id, owner)) or thread_id in request.app.state.runs.runs:
        return _subscription(request, thread_id, owner)
    request.app.state.runs.admit(owner)
    if store is not None:
        project_id = body.project_id
        if project_id is None:
            project_id = await store.ensure_scratch_project(user.id)
        elif not project_id:
            raise HTTPException(422, "project_id is required")
        if await store.project_for_user(project_id, user.id) is None:
            raise HTTPException(404, "no such project")
        if not await store.create_thread(thread_id, project_id, user.id, body.prompt or "BOM validation",
                run_kind="bom", run_payload={"rows": [row.__dict__ for row in rows], "brief": body.prompt}):
            return _subscription(request, thread_id, owner)

    stream = STREAMS[thread_id] = events.EventStream(thread_id)
    return await _start_run(request, thread_id, owner, _run_bom(rows, body.prompt, stream, store))


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


@app.get("/ready")
async def readiness(request: Request):
    settings = Settings.read()
    pool = request.app.state.pool
    if pool is None:
        return JSONResponse({"status": "unavailable" if settings.environment == "production" else "ready", "persistence": "memory"},
                            status_code=503 if settings.environment == "production" else 200)
    try:
        async with asyncio.timeout(3):
            async with pool.connection() as conn:
                await conn.execute("SELECT 1")
    except Exception:
        return JSONResponse({"status": "unavailable", "database": "unreachable"}, status_code=503)
    return {"status": "ready", "persistence": "postgres"}


@app.get("/runtime")
async def runtime(request: Request) -> dict[str, Any]:
    from ..parts import fixtures, research
    return {"accounts": request.app.state.store is not None,
            "persistence": "postgres" if request.app.state.store else "memory",
            "ai": llm.public_status(),
            "research": research.status(),
            "parts": "recorded" if fixtures.replaying() else "live"}


def _owner(user: Any) -> str:
    return user.id if user else "local"


def _thread_id(owner: str, request_id: str | None, fingerprint: str) -> str:
    if request_id:
        return hashlib.sha256(f"{owner}:{request_id}:{fingerprint}".encode()).hexdigest()[:24]
    return uuid.uuid4().hex[:12]


def _subscription(request: Request, thread_id: str, owner: str, after: int = -1) -> StreamingResponse:
    return StreamingResponse(
        request.app.state.runs.subscribe(thread_id, owner, after, request.app.state.store),
        media_type="text/event-stream", headers={**SSE_HEADERS, "X-Thread-ID": thread_id},
    )


async def _start_run(request: Request, thread_id: str, owner: str, producer: AsyncIterator[str],
                     after: int = -1, *, allow_cancelled: bool = False) -> StreamingResponse:
    store = request.app.state.store

    def cleanup() -> None:
        if store:
            STREAMS.pop(thread_id, None)
            BOMS.pop(thread_id, None)
            FINDING_RECORDERS.pop(thread_id, None)

    if store:
        recorder = FindingRecorder()
        for event in await store.run_events(thread_id):
            recorder.feed(event)
        FINDING_RECORDERS[thread_id] = recorder
    await request.app.state.runs.start(thread_id, owner, producer, store, cleanup, last_seq=after,
                                     allow_cancelled=allow_cancelled)
    return _subscription(request, thread_id, owner, after)


@app.post("/design")
async def design(body: DesignRequest, request: Request) -> StreamingResponse:
    store = request.app.state.store
    user = await _signed_in(request)
    owner = _owner(user)
    thread_id = _thread_id(owner, body.request_id, f"design:{body.project_id}:{body.prompt}")
    if store and await store.thread_for_user(thread_id, owner):
        return _subscription(request, thread_id, owner)
    if thread_id in request.app.state.runs.runs:
        return _subscription(request, thread_id, owner)
    request.app.state.runs.admit(owner)
    if store is not None:
        project_id = body.project_id
        if project_id is None:
            project_id = await store.ensure_scratch_project(owner)
        elif not project_id:
            raise HTTPException(422, "project_id is required")
        if await store.project_for_user(project_id, owner) is None:
            raise HTTPException(404, "no such project")
        if not await store.create_thread(thread_id, project_id, owner, body.prompt,
                run_kind="brief", run_payload={"prompt": body.prompt}):
            return _subscription(request, thread_id, owner)
    STREAMS[thread_id] = events.EventStream(thread_id)
    return await _start_run(request, thread_id, owner,
        _run(request.app.state.graph, thread_id, {"prompt": body.prompt}, store, user.id if user else None))


@app.post("/resume")
async def resume(body: ResumeRequest, request: Request) -> StreamingResponse:
    store = request.app.state.store
    user = await _signed_in(request)
    owner = _owner(user)
    stream = STREAMS.get(body.thread_id)
    if store:
        thread = await store.thread_for_user(body.thread_id, owner)
        if thread is None:
            raise HTTPException(404, "unknown thread")
        if thread.status != "awaiting":
            raise HTTPException(409, "This run is not waiting for an answer.")
        stream = events.EventStream(body.thread_id, last_seq=thread.last_seq)
    elif stream is None:
        raise HTTPException(404, "unknown thread")
    request.app.state.runs.admit(owner)
    STREAMS[body.thread_id] = stream
    return await _start_run(request, body.thread_id, owner,
        _run(request.app.state.graph, body.thread_id, Command(resume=body.answer), store, user.id if user else None),
        stream.last_seq, allow_cancelled=True)


@app.post("/threads/{thread_id}/continue")
async def continue_thread(thread_id: str, request: Request) -> StreamingResponse:
    store = auth.store_of(request)
    user = await auth.current_user(request)
    await store.recover_stale_runs()
    thread = await store.thread_for_user(thread_id, user.id)
    if thread is None:
        raise HTTPException(404, "unknown thread")
    if thread.status not in {"abandoned", "error"}:
        raise HTTPException(409, "only stopped runs can be continued")
    original = await store.run_input(thread_id, user.id)
    if original and original["kind"] == "bom":
        request.app.state.runs.admit(user.id)
        stream = STREAMS[thread_id] = events.EventStream(thread_id, last_seq=thread.last_seq)
        payload = original["payload"]
        return await _start_run(request, thread_id, user.id,
            _run_bom([bom.BomRow(**row) for row in payload["rows"]], payload.get("brief"), stream, store), thread.last_seq,
            allow_cancelled=True)
    snapshot = await _checkpoint_snapshot(request.app.state.graph, thread_id)
    if snapshot is None or not snapshot.next:
        raise HTTPException(409, "This run has no unfinished checkpoint. Start a new design.")
    request.app.state.runs.admit(user.id)
    STREAMS[thread_id] = events.EventStream(thread_id, last_seq=thread.last_seq)
    return await _start_run(request, thread_id, user.id,
        _run(request.app.state.graph, thread_id, None, store, user.id), thread.last_seq, allow_cancelled=True)


@app.get("/threads/{thread_id}/events")
async def reconnect(thread_id: str, request: Request, after: int = Query(-1, ge=-1)) -> StreamingResponse:
    store = request.app.state.store
    user = await _signed_in(request)
    owner = _owner(user)
    if store:
        if await store.thread_for_user(thread_id, owner) is None:
            raise HTTPException(404, "unknown thread")
    elif thread_id not in request.app.state.runs.runs:
        raise HTTPException(404, "unknown thread")
    return _subscription(request, thread_id, owner, after)


@app.post("/threads/{thread_id}/cancel", status_code=204)
async def cancel_thread(thread_id: str, request: Request) -> None:
    user = await _signed_in(request)
    store = request.app.state.store
    owner = _owner(user)
    if store and not await store.cancel_run(thread_id, owner):
        raise HTTPException(404, "unknown thread")
    if not await request.app.state.runs.stop(thread_id, owner):
        if not store:
            raise HTTPException(404, "unknown thread")


@app.get("/threads/{thread_id}/board")
async def thread_board(thread_id: str, request: Request) -> dict[str, Any]:
    """Return a finished board without replaying the work that produced it."""
    store = request.app.state.store
    user = await _signed_in(request)

    if store is None:
        raise HTTPException(404, "unknown thread")

    await store.recover_stale_runs()
    thread = await store.thread_for_user(thread_id, user.id)
    if thread is None:
        raise HTTPException(404, "unknown thread")

    revision = await store.thread_revision(thread_id, user.id)
    if revision and thread.status == "done":
        document = revision["document"]
        trace = [event for event in await store.run_events(thread_id) if event.get("type") not in {"bom", "board"}]
        if revision.get("parent_id") or not trace:
            trace = revision_trace(revision, thread_id)
        return {**document["view"], "status": "done", "revision_id": revision["id"],
                "summary": thread.summary, "bom": document["bom"], "checkpoint": "revision",
                "trace": trace, "question": None, "resumable": False}

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
    trace = [event for event in await store.run_events(thread_id) if event.get("type") != "bom"]
    question = _pending_question(snapshot, thread)
    resumable = bool(snapshot and snapshot.next) and thread.status in {"awaiting", "abandoned", "error"}
    original = await store.run_input(thread_id, user.id)
    if original and original["kind"] == "bom" and thread.status in {"abandoned", "error"}:
        resumable = True
    if thread.status == "done" and any(event.get("type") == "board" for event in trace):
        return _bom_only_board(thread, trace, question, resumable,
                               checkpoint="available" if values else "unavailable")
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
    return events.board_slot(slot)


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
    recorded = recorded_board(trace or [])
    return {
        "status": thread.status,
        "summary": thread.summary,
        "slots": [],
        "edges": [],
        **(recorded or {}),
        "bom": _board_bom(rows),
        "checkpoint": "recorded" if recorded and checkpoint != "available" else checkpoint,
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
        revision = await store.thread_revision(thread_id, user.id) if thread else None
        if revision:
            rows = revision["document"]["bom"]["rows"]

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
    return [json.loads(line) for line in WALKTHROUGH.read_text(encoding="utf-8").splitlines() if line.strip()]


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
