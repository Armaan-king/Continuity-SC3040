"""Ownership: whose projects, whose threads, whose board.

Skipped unless `CONTINUITY_TEST_DB` is set.

Two groups of tests here, and they fail very differently.

**The ownership ones fail silently in production.** `/resume` and `/export` used to take
a thread id and trust it. With one user that is indistinguishable from correct; with two
it hands over someone else's board. Every one of these asserts a 404 rather than a 403,
because a 403 confirms the thread exists.

**The restart one fails invisibly on the client.** A resumed stream that starts its
numbering again is not rejected — the client drops every frame at or below its high-water
mark, so the run renders as a hang with nothing in the log. `test_resume_survives_a_restart`
is the reason `last_seq` is persisted at all.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager

import httpx
import pytest

from continuity.api import app as app_module
from continuity.api.app import app
from continuity.api import events
from continuity.api.store import Store

DB_URL = os.environ.get("CONTINUITY_TEST_DB")

pytestmark = pytest.mark.skipif(
    not DB_URL, reason="set CONTINUITY_TEST_DB to run the ownership tests"
)

UNRESOLVED = "solar powered weather station with a sensor"
"""Interrupts on the supply question, so a run can be paused and resumed."""

DEMO = "temp and humidity sensor, wifi and ble, usb-c powered with li-ion backup, small oled"


def run(coro):
    return asyncio.run(coro)


@asynccontextmanager
async def a_store():
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(DB_URL, min_size=1, max_size=3, open=False) as pool:
        await pool.open()
        store = Store(pool)
        await store.setup()
        async with pool.connection() as conn:
            await conn.execute("TRUNCATE users, sessions, projects, threads CASCADE")

        previous = app.state.store
        app.state.store = store
        app_module.STREAMS.clear()
        app_module.BOMS.clear()
        try:
            yield store
        finally:
            app.state.store = previous


def a_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30.0
    )


@asynccontextmanager
async def signed_in(email: str = "sparsh@example.com"):
    """A client with an account and its session cookie already held."""
    async with a_client() as http:
        await http.post("/auth/register", json={"email": email, "password": "a-good-password"})
        yield http


async def frames_of(http: httpx.AsyncClient, path: str, payload: dict) -> list[dict]:
    async with http.stream("POST", path, json=payload) as response:
        assert response.status_code == 200, await response.aread()
        return [
            json.loads(line[6:])
            async for line in response.aiter_lines()
            if line.startswith("data: ")
        ]


# ── the routes need an account ────────────────────────────────────────────────


def test_projects_need_an_account():
    async def go():
        async with a_store():
            async with a_client() as http:
                return await http.get("/projects")

    assert run(go()).status_code == 401


def test_designing_needs_an_account():
    async def go():
        async with a_store():
            async with a_client() as http:
                return await http.post("/design", json={"prompt": DEMO, "project_id": "x"})

    assert run(go()).status_code == 401


# ── projects ──────────────────────────────────────────────────────────────────


def test_a_created_project_is_listed():
    async def go():
        async with a_store():
            async with signed_in() as http:
                created = await http.post("/projects", json={"name": "Weather station"})
                return created, await http.get("/projects")

    created, listed = run(go())
    assert created.status_code == 201
    assert [p["name"] for p in listed.json()] == ["Weather station"]


def test_projects_list_for_their_owner_only():
    async def go():
        async with a_store():
            async with signed_in("mine@example.com") as mine:
                await mine.post("/projects", json={"name": "Mine"})
            async with signed_in("theirs@example.com") as theirs:
                await theirs.post("/projects", json={"name": "Theirs"})
                return await theirs.get("/projects")

    assert [p["name"] for p in run(go()).json()] == ["Theirs"]


def test_another_users_project_is_a_404():
    async def go():
        async with a_store():
            async with signed_in("mine@example.com") as mine:
                project = (await mine.post("/projects", json={"name": "Mine"})).json()
            async with signed_in("theirs@example.com") as theirs:
                return await theirs.get(f"/projects/{project['id']}")

    assert run(go()).status_code == 404


def test_a_project_cannot_be_deleted_by_someone_else():
    async def go():
        async with a_store():
            async with signed_in("mine@example.com") as mine:
                project = (await mine.post("/projects", json={"name": "Mine"})).json()
                async with signed_in("theirs@example.com") as theirs:
                    stolen = await theirs.delete(f"/projects/{project['id']}")
                return stolen, await mine.get("/projects")

    stolen, still_mine = run(go())
    assert stolen.status_code == 404
    assert len(still_mine.json()) == 1


# ── a run belongs to a project, and to a person ───────────────────────────────


def test_a_run_records_a_thread_against_its_project():
    async def go():
        async with a_store() as store:
            async with signed_in() as http:
                project = (await http.post("/projects", json={"name": "P"})).json()
                frames = await frames_of(http, "/design", {"prompt": DEMO, "project_id": project["id"]})
                me = (await http.get("/auth/me")).json()
                return await store.thread_for_user(frames[0]["thread_id"], me["id"])

    thread = run(go())
    assert thread is not None
    assert thread.prompt == DEMO
    assert thread.status == "done"
    assert thread.bom, "the finished board should have been recorded"


def test_designing_without_a_project_uses_one_owned_scratch_project():
    async def go():
        async with a_store() as store:
            async with signed_in() as http:
                me = (await http.get("/auth/me")).json()
                first = await frames_of(http, "/design", {"prompt": DEMO})
                second = await frames_of(http, "/design", {"prompt": DEMO})
                projects = (await http.get("/projects")).json()
                scratch = [project for project in projects if project["name"] == "Scratch designs"]
                threads = await store.threads_for_project(scratch[0]["id"], me["id"])
                return first, second, scratch, threads, me

    first, second, scratch, threads, me = run(go())
    assert len(scratch) == 1, "a second call must not create a second scratch project"
    assert {thread.id for thread in threads} == {first[0]["thread_id"], second[0]["thread_id"]}
    assert all(thread.user_id == me["id"] for thread in threads)
    assert all(thread.project_id == scratch[0]["id"] for thread in threads)


def test_designing_into_another_users_project_is_a_404():
    async def go():
        async with a_store():
            async with signed_in("mine@example.com") as mine:
                project = (await mine.post("/projects", json={"name": "Mine"})).json()
            async with signed_in("theirs@example.com") as theirs:
                return await theirs.post(
                    "/design", json={"prompt": DEMO, "project_id": project["id"]}
                )

    assert run(go()).status_code == 404


def test_another_users_thread_cannot_be_resumed():
    async def go():
        async with a_store():
            async with signed_in("mine@example.com") as mine:
                project = (await mine.post("/projects", json={"name": "Mine"})).json()
                frames = await frames_of(
                    mine, "/design", {"prompt": UNRESOLVED, "project_id": project["id"]}
                )
                thread_id = frames[0]["thread_id"]
            async with signed_in("theirs@example.com") as theirs:
                return await theirs.post(
                    "/resume", json={"thread_id": thread_id, "answer": "USB-C 5V"}
                )

    assert run(go()).status_code == 404


def test_another_users_board_cannot_be_exported():
    async def go():
        async with a_store():
            async with signed_in("mine@example.com") as mine:
                project = (await mine.post("/projects", json={"name": "Mine"})).json()
                frames = await frames_of(
                    mine, "/design", {"prompt": DEMO, "project_id": project["id"]}
                )
                thread_id = frames[0]["thread_id"]
                mine_export = await mine.get(f"/export/{thread_id}.csv")
            async with signed_in("theirs@example.com") as theirs:
                return mine_export, await theirs.get(f"/export/{thread_id}.csv")

    owned, stolen = run(go())
    assert owned.status_code == 200
    assert stolen.status_code == 404


# ── the point of persisting anything ──────────────────────────────────────────


def test_resume_survives_a_restart():
    """Clearing STREAMS is what a process restart looks like from the database's side."""

    async def go():
        async with a_store():
            async with signed_in() as http:
                project = (await http.post("/projects", json={"name": "P"})).json()
                first = await frames_of(
                    http, "/design", {"prompt": UNRESOLVED, "project_id": project["id"]}
                )
                thread_id = first[0]["thread_id"]

                app_module.STREAMS.clear()  # the process died here

                second = await frames_of(
                    http, "/resume", {"thread_id": thread_id, "answer": "USB-C 5V"}
                )
                return first, second

    first, second = run(go())
    assert first[-1]["type"] == "question"
    assert second[0]["seq"] == first[-1]["seq"] + 1, "the client drops anything it has seen"
    assert len({f["seq"] for f in first + second}) == len(first + second)
    assert second[-1]["type"] == "done"


def test_a_paused_run_is_recorded_as_awaiting():
    async def go():
        async with a_store() as store:
            async with signed_in() as http:
                project = (await http.post("/projects", json={"name": "P"})).json()
                frames = await frames_of(
                    http, "/design", {"prompt": UNRESOLVED, "project_id": project["id"]}
                )
                me = (await http.get("/auth/me")).json()
                return await store.thread_for_user(frames[0]["thread_id"], me["id"])

    thread = run(go())
    assert thread.status == "awaiting"
    assert thread.last_seq >= 0


# ── onboarding ────────────────────────────────────────────────────────────────


def test_the_walkthrough_runs_once_and_marks_the_account():
    async def go():
        async with a_store():
            async with signed_in() as http:
                before = (await http.get("/auth/me")).json()["onboarded"]
                frames = await frames_of(http, "/design/demo", {})
                after = (await http.get("/auth/me")).json()["onboarded"]
                return before, frames, after

    before, frames, after = run(go())
    assert before is False
    assert after is True
    assert frames[-1]["type"] == "done"
    assert any(f["type"] == "plan" for f in frames)
    # The walkthrough exists to show the engine refusing to certify something. A run
    # that passes every check teaches the interface and nothing about the product.
    assert any(f["type"] == "conflict" for f in frames)
    assert any(f["type"] == "repair" for f in frames)
    assert [f["seq"] for f in frames] == list(range(len(frames))), "renumbered onto this thread"
    assert len({f["thread_id"] for f in frames}) == 1


def test_the_walkthrough_leaves_a_real_project_behind():
    async def go():
        async with a_store():
            async with signed_in() as http:
                await frames_of(http, "/design/demo", {})
                return await http.get("/projects")

    projects = run(go()).json()
    assert len(projects) == 1


def test_the_walkthrough_is_idempotent():
    """Called twice, it replays into the same thread rather than making a second project.

    Not a nicety. React re-runs effects in development, so the route calls this twice on
    every mount, and the create-or-409 version handed each new account two "Welcome to
    Continuity" projects — the first abandoned mid-stream and stuck on RUNNING for ever.
    """

    async def go():
        async with a_store():
            async with signed_in() as http:
                first = await frames_of(http, "/design/demo", {})
                second = await frames_of(http, "/design/demo", {})
                projects = (await http.get("/projects")).json()
                return first, second, projects

    first, second, projects = run(go())
    assert len(projects) == 1, "a second call must not create a second project"
    assert first[0]["thread_id"] == second[0]["thread_id"]
    assert [f["seq"] for f in second] == list(range(len(second))), "a replay renumbers from 0"
    assert second[-1]["type"] == "done"


# ── what the engine reported, carried through to the dashboard ────────────────


def test_a_finished_run_records_the_engines_own_summary():
    async def go():
        async with a_store() as store:
            async with signed_in() as http:
                project = (await http.post("/projects", json={"name": "P"})).json()
                frames = await frames_of(
                    http, "/design", {"prompt": DEMO, "project_id": project["id"]}
                )
                me = (await http.get("/auth/me")).json()
                thread = await store.thread_for_user(frames[0]["thread_id"], me["id"])
                done = next(f for f in frames if f["type"] == "done")
                return thread.summary, done["summary"]

    stored, emitted = run(go())
    assert stored == emitted, "stored verbatim, never recomputed"
    assert stored["slots"] == stored["placed"], "the demo board is complete"


def test_the_walkthrough_records_the_conflicts_it_resolved():
    """The dashboard's whole reason for having this: the walkthrough is not a clean run."""

    async def go():
        async with a_store() as store:
            async with signed_in() as http:
                await frames_of(http, "/design/demo", {})
                me = (await http.get("/auth/me")).json()
                project = (await http.get("/projects")).json()[0]
                threads = await store.threads_for_project(project["id"], me["id"])
                return threads[0].summary

    summary = run(go())
    assert summary["conflicts_resolved"] == 3
    assert summary["slots"] == summary["placed"] == 4


def test_the_threads_endpoint_exposes_the_summary():
    async def go():
        async with a_store():
            async with signed_in() as http:
                project = (await http.post("/projects", json={"name": "P"})).json()
                await frames_of(http, "/design", {"prompt": DEMO, "project_id": project["id"]})
                return await http.get(f"/projects/{project['id']}/threads")

    threads = run(go()).json()
    assert len(threads) == 1
    assert threads[0]["summary"]["conflicts_resolved"] >= 0
    assert threads[0]["status"] == "done"


def test_a_paused_run_has_no_summary_to_report():
    """A summary appears only when the engine has actually finished a board."""

    async def go():
        async with a_store():
            async with signed_in() as http:
                project = (await http.post("/projects", json={"name": "P"})).json()
                await frames_of(
                    http, "/design", {"prompt": UNRESOLVED, "project_id": project["id"]}
                )
                return await http.get(f"/projects/{project['id']}/threads")

    threads = run(go()).json()
    assert threads[0]["status"] == "awaiting"
    assert threads[0]["summary"] is None


# ── reopening a completed board ──────────────────────────────────────────────


def test_a_finished_thread_hydrates_its_checkpointed_board():
    async def go():
        async with a_store():
            async with signed_in() as http:
                project = (await http.post("/projects", json={"name": "P"})).json()
                frames = await frames_of(
                    http, "/design", {"prompt": DEMO, "project_id": project["id"]}
                )
                board = await http.get(f"/threads/{frames[0]['thread_id']}/board")
                return board, frames

    board, frames = run(go())
    body = board.json()

    assert board.status_code == 200
    assert body["status"] == "done"
    assert body["checkpoint"] == "available"
    assert len(body["slots"]) == 4
    assert all(slot["part"] is not None for slot in body["slots"])
    assert body["edges"]
    assert all(edge["status"] in {"pass", "conflict", "unchecked"} for edge in body["edges"])
    # Restored with the board, not only announced during the run: without it the client has
    # no node for a supply edge to start from and drops every one of them, so a reopened
    # project would show its regulator floating again.
    assert body["supply"]["id"] == "__supply"
    assert body["supply"]["voltage"] > 0
    assert {edge["from"] for edge in body["edges"]} & {"__supply"}
    assert body["bom"]["rows"] == next(frame["rows"] for frame in frames if frame["type"] == "bom")
    assert body["summary"] == next(frame["summary"] for frame in frames if frame["type"] == "done")


def test_a_finished_thread_hydrates_its_trace_in_order_without_bom_frames():
    async def go():
        async with a_store():
            async with signed_in() as http:
                project = (await http.post("/projects", json={"name": "P"})).json()
                frames = await frames_of(http, "/design", {"prompt": DEMO, "project_id": project["id"]})
                board = await http.get(f"/threads/{frames[0]['thread_id']}/board")
                return board, frames

    board, frames = run(go())
    assert [event["seq"] for event in board.json()["trace"]] == sorted(
        event["seq"] for event in board.json()["trace"]
    )
    assert not [event for event in board.json()["trace"] if event["type"] == "bom"]
    assert board.json()["trace"] == [event for event in frames if event["type"] != "bom"]


def test_an_awaiting_thread_hydrates_its_board_trace_and_real_pending_question():
    async def go():
        async with a_store():
            async with signed_in() as http:
                project = (await http.post("/projects", json={"name": "P"})).json()
                frames = await frames_of(
                    http, "/design", {"prompt": UNRESOLVED, "project_id": project["id"]}
                )
                board = await http.get(f"/threads/{frames[0]['thread_id']}/board")
                return board, frames

    board, frames = run(go())
    body = board.json()
    question = frames[-1]
    assert body["status"] == "awaiting"
    assert body["resumable"] is True

    # `UNRESOLVED` stops at `clarify`, which runs *before* `plan` — so there is no board
    # yet, and saying so is different from saying the board could not be read. What has to
    # survive is the question, because it is the only thing the user can act on.
    assert body["checkpoint"] == "not_planned"
    assert body["slots"] == []
    assert body["trace"][-1] == question
    assert body["question"] == question


def test_an_abandoned_thread_hydrates_its_board_trace_and_is_resumable():
    async def go():
        async with a_store() as store:
            async with signed_in() as http:
                project = (await http.post("/projects", json={"name": "P"})).json()
                frames = await frames_of(http, "/design", {"prompt": DEMO, "project_id": project["id"]})
                me = (await http.get("/auth/me")).json()
                thread = await store.thread_for_user(frames[0]["thread_id"], me["id"])
                await store.save_progress(thread.id, thread.last_seq, "abandoned")
                return await http.get(f"/threads/{thread.id}/board")

    body = run(go()).json()
    assert body["status"] == "abandoned"
    assert body["slots"]
    assert body["trace"]
    assert body["resumable"] is True


def test_hydrated_parts_use_the_event_part_serialiser():
    async def go():
        async with a_store():
            async with signed_in() as http:
                project = (await http.post("/projects", json={"name": "P"})).json()
                frames = await frames_of(
                    http, "/design", {"prompt": DEMO, "project_id": project["id"]}
                )
                board = await http.get(f"/threads/{frames[0]['thread_id']}/board")
                checkpoint = await app.state.graph.aget_state(
                    {"configurable": {"thread_id": frames[0]["thread_id"]}}
                )
                return board, checkpoint.values["slots"]

    board, slots = run(go())
    expected = {
        slot_id: events._part(slot.part)
        for slot_id, slot in slots.items()
        if slot.part is not None
    }

    assert board.status_code == 200
    assert {slot["id"]: slot["part"] for slot in board.json()["slots"]} == expected


def test_another_users_thread_board_is_a_404():
    async def go():
        async with a_store():
            async with signed_in("mine@example.com") as mine:
                project = (await mine.post("/projects", json={"name": "Mine"})).json()
                frames = await frames_of(
                    mine, "/design", {"prompt": DEMO, "project_id": project["id"]}
                )
            async with signed_in("theirs@example.com") as theirs:
                return await theirs.get(f"/threads/{frames[0]['thread_id']}/board")

    assert run(go()).status_code == 404


def test_an_unknown_thread_board_is_a_404():
    async def go():
        async with a_store():
            async with signed_in() as http:
                return await http.get("/threads/does-not-exist/board")

    assert run(go()).status_code == 404


def test_a_running_thread_is_not_hydrated():
    async def go():
        async with a_store() as store:
            async with signed_in() as http:
                me = (await http.get("/auth/me")).json()
                project = await store.create_project(me["id"], "P")
                await store.create_thread("still-running", project.id, me["id"], "A board")
                return await http.get("/threads/still-running/board")

    response = run(go())

    assert response.status_code == 200
    assert response.json() == {
        "status": "running",
        "summary": None,
        "slots": [],
        "edges": [],
        "bom": None,
        "checkpoint": "not_loaded",
        "trace": [],
        "question": None,
        "resumable": False,
    }


def test_another_users_thread_cannot_be_continued():
    async def go():
        async with a_store() as store:
            async with signed_in("mine@example.com") as mine:
                project = (await mine.post("/projects", json={"name": "Mine"})).json()
                frames = await frames_of(mine, "/design", {"prompt": DEMO, "project_id": project["id"]})
                me = (await mine.get("/auth/me")).json()
                thread = await store.thread_for_user(frames[0]["thread_id"], me["id"])
                await store.save_progress(thread.id, thread.last_seq, "abandoned")
            async with signed_in("theirs@example.com") as theirs:
                return await theirs.post(f"/threads/{thread.id}/continue")

    assert run(go()).status_code == 404


@pytest.mark.parametrize("status", ("running", "awaiting"))
def test_continue_refuses_non_continuable_statuses(status):
    async def go():
        async with a_store() as store:
            async with signed_in() as http:
                me = (await http.get("/auth/me")).json()
                project = await store.create_project(me["id"], "P")
                await store.create_thread("cannot-continue", project.id, me["id"], "A board")
                await store.save_progress("cannot-continue", 4, status)
                return await http.post("/threads/cannot-continue/continue")

    assert run(go()).status_code == 409


def test_continue_reenters_with_none_and_keeps_the_persisted_sequence():
    class ContinuationGraph:
        def __init__(self):
            self.payloads = []

        async def astream(self, payload, config, stream_mode):
            self.payloads.append(payload)
            yield "custom", config["configurable"]["events"].reasoning(None, "continued")

    async def go():
        async with a_store() as store:
            async with signed_in() as http:
                me = (await http.get("/auth/me")).json()
                project = await store.create_project(me["id"], "P")
                await store.create_thread("continue-me", project.id, me["id"], "A board")
                await store.save_progress("continue-me", 41, "abandoned")
                graph = ContinuationGraph()
                previous = app.state.graph
                app.state.graph = graph
                try:
                    frames = await frames_of(http, "/threads/continue-me/continue", {})
                finally:
                    app.state.graph = previous
                return frames, graph.payloads

    frames, payloads = run(go())
    assert payloads == [None]
    assert frames[0]["seq"] == 42


def test_two_concurrent_walkthrough_requests_make_one_project():
    """React re-runs effects in development, so both requests land within a millisecond.

    A find-then-create loses that race with itself: every new account got two "Welcome to
    Continuity" projects, the first abandoned mid-stream and stuck showing RUNNING.
    """

    async def go():
        async with a_store():
            async with signed_in() as http:
                await asyncio.gather(
                    frames_of(http, "/design/demo", {}),
                    frames_of(http, "/design/demo", {}),
                )
                return (await http.get("/projects")).json()

    assert len(run(go())) == 1
