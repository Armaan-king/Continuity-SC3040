"""Slot-search prefetching stays outside LangGraph's checkpointed state."""

from __future__ import annotations

import asyncio
import json

from continuity.api import app as app_module
from continuity.api.app import _run
from continuity.api.events import EventStream, _encode
from continuity.engine.models import Requirements
from continuity.graph import nodes, sourcing
from continuity.parts.search import Candidate
from continuity.planner.plan import Plan
from tests import parts
from tests.boards import slot


def _candidate(name: str) -> Candidate:
    return Candidate(
        lcsc=f"C-{name}",
        mpn=name,
        manufacturer="Test",
        description="Test part",
        package="SOT-23-5",
        category="Regulator",
        subcategory="Regulator",
        stock=100,
        unit_price=0.1,
        library_type="basic",
    )


def _plan(count: int) -> Plan:
    slots = {
        f"part_{number}": slot(
            f"part_{number}",
            constraint={"category": f"category-{number}"},
        )
        for number in range(count)
    }
    return Plan(
        requirements=Requirements(),
        slots=slots,
        rails=[],
        queries={slot_id: f"query-{slot_id}" for slot_id in slots},
        links=(),
        order=tuple(slots),
    )


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id, "events": EventStream(thread_id)}}


async def _start(thread_id: str) -> dict:
    parsed = await nodes.parse_requirements({"prompt": "brief"}, _config(thread_id))
    return {**parsed, **nodes.plan(parsed, _config(thread_id))}


async def _place_all(state: dict, thread_id: str) -> dict:
    while state["pending"]:
        state = {**state, **await nodes.select(state, _config(thread_id))}
    return state


def test_prefetches_each_slot_once_and_select_consumes_the_result(monkeypatch):
    async def go():
        board_plan = _plan(3)
        calls = []

        async def plan_board(prompt):
            return board_plan

        async def find(query, *, constraint=None, **_context):
            calls.append((query, constraint))
            return [_candidate(query)]

        monkeypatch.setattr(nodes.planner, "plan_board", plan_board)
        monkeypatch.setattr(nodes, "_emit", lambda event: None)
        monkeypatch.setattr(sourcing, "find", find)
        monkeypatch.setattr(sourcing, "choose", lambda candidate: _immediate(parts.ldo_600ma()))

        state = await _start("once")
        await _place_all(state, "once")
        return calls

    calls = asyncio.run(go())

    assert [query for query, _ in calls] == ["query-part_0", "query-part_1", "query-part_2"]


def test_prefetches_all_slot_searches_concurrently_before_placement(monkeypatch):
    async def go():
        board_plan = _plan(3)
        entered: list[str] = []
        release = asyncio.Event()

        async def plan_board(prompt):
            return board_plan

        async def find(query, *, constraint=None, **_context):
            entered.append(query)
            await release.wait()
            return [_candidate(query)]

        monkeypatch.setattr(nodes.planner, "plan_board", plan_board)
        monkeypatch.setattr(nodes, "_emit", lambda event: None)
        monkeypatch.setattr(sourcing, "find", find)
        monkeypatch.setattr(sourcing, "choose", lambda candidate: _immediate(parts.ldo_600ma()))

        state = await _start("overlap")
        await asyncio.sleep(0)
        all_entered_before_release = list(entered)
        release.set()
        await _place_all(state, "overlap")
        return all_entered_before_release

    assert asyncio.run(go()) == ["query-part_0", "query-part_1", "query-part_2"]


def test_prefetch_narrates_its_start_before_a_slow_search(monkeypatch):
    async def go():
        board_plan = _plan(1)
        emitted: list[dict] = []
        search_started = asyncio.Event()
        release = asyncio.Event()

        async def plan_board(prompt):
            return board_plan

        async def find(query, *, constraint=None, **_context):
            search_started.set()
            await release.wait()
            return [_candidate(query)]

        monkeypatch.setattr(nodes.planner, "plan_board", plan_board)
        monkeypatch.setattr(nodes, "_emit", emitted.append)
        monkeypatch.setattr(sourcing, "find", find)

        config = _config("prefetch-narration")
        parsed = await nodes.parse_requirements({"prompt": "brief"}, config)
        await search_started.wait()
        before_completion = list(emitted)
        release.set()
        await asyncio.sleep(0)
        state = {**parsed, **nodes.plan(parsed, config)}
        monkeypatch.setattr(sourcing, "choose", lambda candidate: _immediate(parts.ldo_600ma()))
        await nodes.select(state, config)
        nodes.clear_prefetches("prefetch-narration")
        return before_completion, emitted

    before_completion, frames = asyncio.run(go())

    assert before_completion[-1]["text"] == "In the background, searching JLCPCB for “query-part_0”."
    assert [frame["seq"] for frame in frames] == list(range(len(frames)))
    start = next(
        index
        for index, frame in enumerate(frames)
        if frame.get("text") == "In the background, searching JLCPCB for “query-part_0”."
    )
    finish = next(
        index
        for index, frame in enumerate(frames)
        if frame.get("text") == "1 viable candidate."
    )
    assert start < finish


def test_cancelled_prefetch_never_claims_that_its_search_finished(monkeypatch):
    async def go():
        board_plan = _plan(1)
        emitted: list[dict] = []
        search_started = asyncio.Event()

        async def plan_board(prompt):
            return board_plan

        async def find(query, *, constraint=None, **_context):
            search_started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(nodes.planner, "plan_board", plan_board)
        monkeypatch.setattr(nodes, "_emit", emitted.append)
        monkeypatch.setattr(sourcing, "find", find)

        await nodes.parse_requirements({"prompt": "brief"}, _config("cancelled-prefetch"))
        await search_started.wait()
        nodes.clear_prefetches("cancelled-prefetch", cancel=True)
        await asyncio.sleep(0)
        return emitted

    frames = asyncio.run(go())

    assert [frame["text"] for frame in frames if frame["type"] == "reasoning"] == [
        "Reading the brief.",
        "1 parts to source: Part_0.",
        "In the background, searching JLCPCB for “query-part_0”.",
    ]


def test_failed_prefetch_logs_and_falls_back_to_a_live_search(monkeypatch, caplog):
    async def go():
        board_plan = _plan(1)
        calls = 0

        async def plan_board(prompt):
            return board_plan

        async def find(query, *, constraint=None, **_context):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("prefetch unavailable")
            return [_candidate(query)]

        monkeypatch.setattr(nodes.planner, "plan_board", plan_board)
        monkeypatch.setattr(nodes, "_emit", lambda event: None)
        monkeypatch.setattr(sourcing, "find", find)
        monkeypatch.setattr(sourcing, "choose", lambda candidate: _immediate(parts.ldo_600ma()))

        state = await _start("fallback")
        state = {**state, **await nodes.select(state, _config("fallback"))}
        return calls, state

    calls, state = asyncio.run(go())

    assert calls == 2
    assert state["slots"]["part_0"].part is not None
    assert "Prefetch failed" in caplog.text


def test_empty_registry_resumes_with_live_searches(monkeypatch):
    async def go():
        calls = []

        async def find(query, *, constraint=None, **_context):
            calls.append(query)
            return [_candidate(query)]

        monkeypatch.setattr(nodes, "_emit", lambda event: None)
        monkeypatch.setattr(sourcing, "find", find)
        monkeypatch.setattr(sourcing, "choose", lambda candidate: _immediate(parts.ldo_600ma()))
        board_plan = _plan(2)
        state = {
            "plan": board_plan,
            "slots": board_plan.slots,
            "pending": list(board_plan.order),
        }
        await _place_all(state, "resumed-in-new-process")
        return calls

    assert asyncio.run(go()) == ["query-part_0", "query-part_1"]


def test_repair_researches_instead_of_consuming_a_prefetch(monkeypatch):
    async def go():
        board_plan = _plan(1)
        calls = []

        async def plan_board(prompt):
            return board_plan

        async def find(query, *, constraint=None, **_context):
            calls.append((query, constraint))
            return [_candidate(f"{query}-{len(calls)}")]

        monkeypatch.setattr(nodes.planner, "plan_board", plan_board)
        monkeypatch.setattr(nodes, "_emit", lambda event: None)
        monkeypatch.setattr(sourcing, "find", find)
        monkeypatch.setattr(sourcing, "choose", lambda candidate: _immediate(parts.ldo_600ma()))
        state = await _start("repair")
        state = {**state, **await nodes.select(state, _config("repair"))}
        state.update({"constraint": {"package": "SOT-23-5"}, "conflicts_resolved": 0})
        updated = await nodes.apply(state, _config("repair"))
        return calls, updated

    calls, updated = asyncio.run(go())

    assert len(calls) == 2
    assert calls[1][1] == {"category": "category-0", "package": "SOT-23-5"}
    assert updated["slots"]["part_0"].part is not None


def test_finalizing_clears_the_thread_prefetch_registry(monkeypatch):
    async def go():
        board_plan = _plan(1)

        async def plan_board(prompt):
            return board_plan

        async def find(query, *, constraint=None, **_context):
            return [_candidate(query)]

        monkeypatch.setattr(nodes.planner, "plan_board", plan_board)
        monkeypatch.setattr(nodes, "_emit", lambda event: None)
        monkeypatch.setattr(sourcing, "find", find)
        state = await _start("complete")
        await asyncio.sleep(0)
        nodes.finalize({**state, "slots": board_plan.slots}, _config("complete"))
        return nodes.PREFETCHES

    assert "complete" not in asyncio.run(go())


def test_parse_state_contains_no_async_objects(monkeypatch):
    async def go():
        board_plan = _plan(1)

        async def plan_board(prompt):
            return board_plan

        async def find(query, *, constraint=None, **_context):
            return [_candidate(query)]

        monkeypatch.setattr(nodes.planner, "plan_board", plan_board)
        monkeypatch.setattr(nodes, "_emit", lambda event: None)
        monkeypatch.setattr(sourcing, "find", find)
        state = await nodes.parse_requirements({"prompt": "brief"}, _config("serial"))
        await asyncio.sleep(0)
        nodes.clear_prefetches("serial", cancel=True)
        return state

    state = asyncio.run(go())

    assert not any(asyncio.isfuture(value) or asyncio.iscoroutine(value) for value in state.values())
    json.loads(json.dumps(state, default=_encode))


def test_abandoning_a_stream_cancels_and_removes_prefetches():
    class BlockingGraph:
        async def astream(self, payload, config, stream_mode):
            stream = config["configurable"]["events"]
            yield "custom", stream.reasoning(None, "started")
            await asyncio.Event().wait()

    async def go():
        thread_id = "abandoned-prefetch"
        search = asyncio.create_task(asyncio.Event().wait())
        nodes.PREFETCHES[thread_id] = {"part": search}
        app_module.STREAMS[thread_id] = EventStream(thread_id)
        run = _run(BlockingGraph(), thread_id, {"prompt": "brief"})
        await anext(run)
        await run.aclose()
        try:
            await search
        except asyncio.CancelledError:
            pass
        return thread_id, search.cancelled()

    thread_id, cancelled = asyncio.run(go())

    assert cancelled
    assert thread_id not in nodes.PREFETCHES


async def _immediate(value):
    return value
