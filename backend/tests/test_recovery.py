"""Real interrupt/resume recovery, with provider responses replaced by fixtures."""
import asyncio
import json
from dataclasses import asdict, replace

import pytest
from langgraph.types import Command

from continuity.api.events import EventStream
from continuity.engine import policy, rules
from continuity.engine.models import Board, Requirements, Slot, Rail
from continuity.graph import nodes, recovery, sourcing
from continuity.graph.build import build
from continuity.planner import plan as planner, revision, topology
from tests import parts
from tests.conftest import _as_candidate


def state_without_controller():
    slots = {
        "regulator": Slot("regulator", "Regulator", "power", part=parts.ap2112k()),
        "sensor": Slot("sensor", "Light Sensor", "peripherals", part=parts.sht31(), repair_count=4,
                       constraint={"category": "sensor"}),
    }
    req = Requirements(input_source="usb-5v", current_margin=0.2)
    plan = planner.Plan(req, slots, [Rail("3V3", 3.3, "regulator", ("sensor",))],
                        {"sensor": "light sensor"}, (), ("regulator", "sensor"))
    rails = topology.assemble_rails(plan.rails, req, slot_ids=tuple(slots))
    board = Board(req, slots, rails)
    return {"requirements": req, "prompt": "USB powered light monitor", "plan": plan, "slots": slots,
            "rails": rails, "pending": [], "current": "sensor", "verdicts": rules.evaluate(board),
            "candidates": {}, "accepted": [], "escalation": "No controller drives the light sensor."}


def config():
    return {"configurable": {"thread_id": "recovery-test", "events": EventStream("recovery-test")}, "recursion_limit": 150}


def saved(value):
    # Checkpoint serialization represents tuples as lists; compare the persisted facts.
    return json.loads(json.dumps(asdict(value)))


def test_missing_controller_never_spends_four_peripheral_swaps():
    state = state_without_controller()
    board = recovery.board_of(state)
    conflict = next(v for v in rules.failures(state["verdicts"]) if v.rule == "interface_role_match")
    result = policy.plan_resolution(conflict, board, [])
    assert result.escalate and result.legal == ()
    assert "controller" in result.reason
    assert recovery.CONTROLLER in recovery.options(state)


def test_unknown_controller_role_is_not_evidence_of_absence():
    state = state_without_controller()
    state["slots"]["mcu"] = Slot("mcu", "MCU", "core", part=parts.esp32s3(role=None), constraint={"category": "mcu"})
    verdicts = rules.interface_role_match(recovery.board_of(state))
    sensor = next(v for v in verdicts if v.subject == "sensor")
    assert sensor.status == "warn" and sensor.unchecked
    assert "unverified" in sensor.detail


def test_full_graph_allows_failed_swap_rollback_then_working_controller(monkeypatch):
    """User decisions bypass the automatic retry cap, but never bypass validation."""
    state = state_without_controller()
    bad = parts.esp32s3(mpn="NO-I2C", interfaces=("SPI",), i_peak=0.02)
    good = parts.esp32s3(mpn="WITH-I2C", i_peak=0.02)
    # Existing controller is wrongly classified; peripheral replacements cannot fix it.
    original = replace(bad, mpn="OLD", role="passive")
    state["slots"]["mcu"] = Slot("mcu", "MCU", "core", part=original,
                                  constraint={"category": "mcu"}, repair_count=7)
    state["plan"] = replace(state["plan"], slots=state["slots"], queries={"mcu": "controller"},
                            rails=[replace(state["plan"].rails[0], members=("sensor", "mcu"))])
    state["rails"] = topology.assemble_rails(state["plan"].rails, state["requirements"], slot_ids=tuple(state["slots"]))
    state["candidates"] = {"mcu": [_as_candidate(bad), _as_candidate(good)]}
    state["verdicts"] = rules.evaluate(recovery.board_of(state))
    picked = []

    async def choose(candidate):
        picked.append(candidate.mpn)
        return {bad.mpn: bad, good.mpn: good}[candidate.mpn]

    monkeypatch.setattr(sourcing, "choose", choose)

    async def go():
        graph, cfg = build(), config()
        await graph.aupdate_state(cfg, state, as_node="validate")
        first = await graph.ainvoke(None, cfg)
        assert first["__interrupt__"]
        selection = await graph.ainvoke(Command(resume=recovery.CHOOSE), cfg)
        assert any(row["slot"] == "mcu" for row in selection["offered_alternatives"])
        choices = await graph.ainvoke(Command(resume="Change component: mcu"), cfg)
        assert picked == []  # A comparison is not a purchase or a selection.
        bad_action = next(row["action"] for row in choices["offered_alternatives"] if row.get("candidate").mpn == bad.mpn)
        failed = await graph.ainvoke(Command(resume=bad_action), cfg)
        assert failed["__interrupt__"]
        assert failed["slots"]["mcu"].part == bad
        assert any(v.rule == "interface_role_match" for v in rules.failures(failed["verdicts"]))
        assert picked == [bad.mpn]  # No automatic override of a manual choice.
        await graph.ainvoke(Command(resume=recovery.CHOOSE), cfg)
        choices = await graph.ainvoke(Command(resume="Change component: mcu"), cfg)
        assert saved(choices["offered_alternatives"][0]["part"]) == saved(original)
        rollback = choices["offered_alternatives"][0]["action"]
        restored = await graph.ainvoke(Command(resume=rollback), cfg)
        assert saved(restored["slots"]["mcu"].part) == saved(original)
        assert rules.failures(restored["verdicts"])  # Old failure was not erased.
        await graph.ainvoke(Command(resume=recovery.CHOOSE), cfg)
        choices = await graph.ainvoke(Command(resume="Change component: mcu"), cfg)
        good_action = next(row["action"] for row in choices["offered_alternatives"] if row.get("candidate") and row["candidate"].mpn == good.mpn)
        completed = await graph.ainvoke(Command(resume=good_action), cfg)
        assert not completed.get("__interrupt__")
        assert completed["slots"]["mcu"].part == good
        assert saved(completed["slots"]["sensor"].part) == saved(state["slots"]["sensor"].part)
        assert not rules.failures(completed["verdicts"])
        assert saved(completed["requirements"]) == saved(state["requirements"])
        assert completed["accepted"] == []
        assert completed["slots"]["mcu"].repair_count == 10
    asyncio.run(go())


def test_exhausted_prompt_rebuilds_structure_and_keeps_selected_sensor(monkeypatch):
    state = state_without_controller()
    emitted = []
    monkeypatch.setattr(nodes, "_emit", emitted.append)
    monkeypatch.setattr(nodes, "interrupt", lambda _: "Add an I2C controller and keep my sensor")

    async def propose(brief, instruction, old, finding):
        assert "keep my sensor" in instruction
        added = Slot("mcu", "Microcontroller", "core", constraint={"category": "mcu"})
        return revision.Revision(replace(old, slots={**old.slots, "mcu": added},
            rails=[replace(old.rails[0], members=("sensor", "mcu"))], links=(("mcu", "sensor"),),
            order=("regulator", "mcu", "sensor")), frozenset(), "Added the missing controller.")

    monkeypatch.setattr(revision, "propose", propose)
    async def go():
        redirected = await nodes.escalate(state, config())
        assert redirected["recovery_action"] == recovery.REDESIGN
        result = await recovery.recover({**state, **redirected}, config())
        assert result["pending"] == ["mcu"]
        assert result["slots"]["sensor"].part == state["slots"]["sensor"].part
        assert result["requirements"] == state["requirements"]
        assert result["verdicts"] == [] and result["revalidate_all"]
        assert any(e["type"] == "plan" and e["replace"] for e in emitted)
    asyncio.run(go())


def test_unavailable_redesign_preserves_board_and_returns_options(monkeypatch):
    state = state_without_controller()
    monkeypatch.setattr(nodes, "_emit", lambda _: None)
    result = asyncio.run(recovery.recover({**state, "recovery_action": recovery.REDESIGN}, config()))
    assert "existing board has been kept" in result["escalation"]
    assert "slots" not in result
    assert recovery.after_recovery(result) == "escalate"


def test_add_controller_is_powered_and_keeps_peripheral(monkeypatch):
    state = state_without_controller()
    monkeypatch.setattr(nodes, "_emit", lambda _: None)
    result = asyncio.run(recovery.recover({**state, "recovery_action": recovery.CONTROLLER}, config()))
    controller = result["pending"][0]
    assert controller in result["rails"]["3V3"].members
    assert (controller, "sensor") in result["plan"].links
    assert result["slots"]["sensor"].part == state["slots"]["sensor"].part


def test_fresh_search_excludes_previous_parts_and_waits_for_user(monkeypatch):
    state = state_without_controller()
    state["verdicts"] = []
    old = state["slots"]["sensor"].part
    new = replace(old, mpn="NEW-SENSOR")
    state["candidates"] = {"sensor": [_as_candidate(old)]}
    async def find(query, **kwargs):
        assert old.mpn.casefold() in kwargs["exclude"]
        return [_as_candidate(old), _as_candidate(new)]
    monkeypatch.setattr(sourcing, "find", find)
    monkeypatch.setattr(nodes, "_emit", lambda _: None)
    result = asyncio.run(recovery.recover({**state, "recovery_action": recovery.SEARCH}, config()))
    assert len(result["offered_alternatives"]) == 1
    assert "NEW-SENSOR" in result["offered_alternatives"][0]["action"]
    assert "slots" not in result
    assert recovery.after_recovery(result) == "escalate"


@pytest.mark.parametrize("response", [None, [], {}, {"unapplied": "nonsense"}])
def test_malformed_redesign_fails_without_mutation(monkeypatch, response):
    async def reply(*args, **kwargs):
        return response
    monkeypatch.setattr(revision.llm, "available", lambda: True)
    monkeypatch.setattr(revision.llm, "complete_json", reply)
    state = state_without_controller()
    with pytest.raises(ValueError, match="board has been kept"):
        asyncio.run(revision.propose("light monitor", "add controller", state["plan"], "missing controller"))


def test_valid_ai_revision_cannot_replace_confirmed_requirements(monkeypatch):
    state = state_without_controller()
    old = state["plan"]
    raw = {"input_source": "12v-barrel", "current_margin": 0.9, "slots": [
        {"id": "regulator", "label": "Regulator", "tier": "power", "query": "3.3V regulator", "category": "regulator"},
        {"id": "sensor", "label": "Light Sensor", "tier": "peripherals", "query": "light sensor", "category": "sensor"},
        {"id": "mcu", "label": "MCU", "tier": "core", "query": "I2C microcontroller", "category": "mcu"},
    ], "rails": [{"id": "3V3", "voltage": 3.3, "source": "regulator", "members": ["sensor", "mcu"]}], "links": [["mcu", "sensor"]]}
    async def reply(*args, **kwargs):
        assert kwargs["max_attempts"] == 1
        return {"plan": raw, "replace_slots": [], "unapplied": [], "explanation": "Added an I2C controller."}
    monkeypatch.setattr(revision.llm, "available", lambda: True)
    monkeypatch.setattr(revision.llm, "complete_json", reply)
    proposed = asyncio.run(revision.propose(state["prompt"], "add a controller", old, "missing controller"))
    assert proposed.plan.requirements == old.requirements
    assert "mcu" in proposed.plan.slots and proposed.plan.links == (("mcu", "sensor"),)
    assert "mcu" not in old.slots


def test_catalogue_failure_keeps_original_selection_and_question(monkeypatch):
    from continuity.parts.mcp import ToolError
    state = {**state_without_controller(), "verdicts": [], "recovery_action": recovery.SEARCH}
    async def fail(*args, **kwargs):
        raise ToolError("raw upstream data must not be shown")
    monkeypatch.setattr(sourcing, "find", fail)
    monkeypatch.setattr(nodes, "_emit", lambda _: None)
    result = asyncio.run(recovery.recover(state, config()))
    assert "slots" not in result and "board was kept" in result["escalation"]
    assert "raw upstream" not in result["escalation"]


def test_alternative_cards_survive_question_reload_without_server_objects():
    from types import SimpleNamespace
    from continuity.api.execution import _pending_question, _interrupt_events
    state = state_without_controller()
    state["offered_alternatives"] = [recovery.catalogue_offer(_as_candidate(parts.sht31()), 1)]
    cards = recovery.public_alternatives(state)
    payload = {"question_id": "escalation", "text": "Choose", "suggestions": [cards[0]["action"]], "alternatives": cards}
    interrupt = SimpleNamespace(value=payload)
    snapshot = SimpleNamespace(tasks=[SimpleNamespace(interrupts=[interrupt])])
    thread = SimpleNamespace(id="t", last_seq=20)
    restored = _pending_question(snapshot, thread)
    streamed = _interrupt_events(EventStream("t"), {"__interrupt__": [interrupt]})[0]
    assert restored["alternatives"] == streamed["alternatives"] == cards
    assert "candidate" not in cards[0]


def test_other_researched_options_remain_available_after_a_trial_without_search(monkeypatch):
    state = state_without_controller()
    row = {"mpn": "WEB-SENSOR", "manufacturer": "Test", "action": "Try alternative 1: WEB-SENSOR",
           "label": "Web Sensor", "summary": "Previously retrieved source passage", "source": "https://example.org/sensor",
           "verification": "Unverified trial", "documents": []}
    state["researched_alternatives"] = {"sensor": [row]}
    state["offered_alternatives"] = [{"action": "Change component: sensor", "slot": "sensor"}]
    monkeypatch.setattr(nodes, "_emit", lambda _: None)
    result = asyncio.run(recovery.recover({**state, "recovery_action": "Change component: sensor"}, config()))
    assert result["offered_alternatives"][0]["mpn"] == "WEB-SENSOR"
    assert "slots" not in result
