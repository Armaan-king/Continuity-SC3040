"""Late component declarations are appended without disturbing the existing board."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from continuity import reviewer
from continuity.api.events import EventStream
from continuity.engine import policy
from continuity.engine.models import Board, Rail, Repair, Requirements, Verdict
from continuity.graph import nodes
from continuity.planner import topology
from tests import parts
from tests.boards import slot


def _state(*, added_slots: int = 0):
    slots = {
        "reg5": slot("reg5", parts.ldo_600ma(), tier="power"),
        "mcu": slot("mcu", parts.esp32s3()),
    }
    rails = topology.assemble_rails(
        [Rail("5V0", 5.0, "reg5", ("mcu",))], Requirements(), slot_ids=tuple(slots)
    )
    return {
        "requirements": Requirements(),
        "plan": SimpleNamespace(queries={}),
        "slots": slots,
        "rails": rails,
        "pending": [],
        "current": "mcu",
        "constraint": {"category": "regulator"},
        "repair_action": "add_part",
        "candidates": {},
        "cursor": {},
        "conflicts_resolved": 2,
        "added_slots": added_slots,
    }


def _config():
    return {"configurable": {"events": EventStream("add-part")}}


def test_add_part_appends_a_slot_sources_it_next_and_keeps_placed_parts(monkeypatch):
    state = _state()
    placed = {slot_id: slot.part for slot_id, slot in state["slots"].items()}
    emitted = []
    monkeypatch.setattr(nodes, "_emit", emitted.append)

    updated = asyncio.run(nodes.apply(state, _config()))

    added_id = next(slot_id for slot_id in updated["slots"] if slot_id not in placed)
    assert added_id == "regulator"
    assert updated["pending"][0] == added_id
    assert {slot_id: slot.part for slot_id, slot in updated["slots"].items() if slot_id in placed} == placed
    assert updated["slots"]["mcu"].repair_count == 1
    assert updated["rails"]["5V0"].members == ("mcu", added_id)
    assert updated["revalidate_all"] is True
    assert nodes.after_apply({**state, **updated}) == "select"
    added = next(event for event in emitted if event["type"] == "slot_added")
    assert added["slot"] == {
        "id": added_id,
        "label": "Regulator",
        "tier": "power",
        "pinned": False,
    }
    assert added["edges"] == [
        {"id": f"pwr-{added_id}", "from": "reg5", "to": added_id,
         "label": "5V0", "kind": "power", "status": "pending"}
    ]
    assert not [event for event in emitted if event["type"] == "plan"]


def test_add_part_mints_a_non_colliding_id(monkeypatch):
    state = _state()
    monkeypatch.setattr(nodes, "_emit", lambda _event: None)
    state["slots"]["regulator"] = slot("regulator", parts.ldo_600ma(), tier="power")
    state["rails"] = topology.assemble_rails(
        [Rail("5V0", 5.0, "reg5", ("mcu", "regulator"))],
        state["requirements"],
        slot_ids=tuple(state["slots"]),
    )

    updated = asyncio.run(nodes.apply(state, _config()))

    assert "regulator_2" in updated["slots"]
    assert updated["slots"]["regulator_2"].label == "Regulator 2"


def test_added_slot_counts_as_declared_and_can_settle_with_the_board(monkeypatch):
    state = _state()
    monkeypatch.setattr(nodes, "_emit", lambda _event: None)
    added = asyncio.run(nodes.apply(state, _config()))
    added_id = next(slot_id for slot_id in added["slots"] if slot_id not in state["slots"])
    added["slots"][added_id] = slot(added_id, parts.ldo_600ma(), tier="power")
    added["pending"] = []
    added["current"] = added_id

    validated = nodes.validate({**state, **added}, _config())

    assert set(validated["slots"]) == {"reg5", "mcu", added_id}
    assert all(slot.status == "pass" for slot in validated["slots"].values())


def test_added_slot_is_filled_before_the_whole_board_is_revalidated(monkeypatch):
    state = _state()
    state["plan"] = SimpleNamespace(queries={"regulator": "3.3V LDO regulator"})
    emitted = []
    monkeypatch.setattr(nodes, "_emit", emitted.append)

    added = asyncio.run(nodes.apply(state, _config()))
    sourced = asyncio.run(nodes.select({**state, **added}, _config()))
    validated = nodes.validate({**state, **added, **sourced}, _config())

    assert sourced["current"] == added["pending"][0]
    assert sourced["source_next"] is False
    assert len([event for event in emitted if event["type"] == "check"]) == len(
        validated["verdicts"]
    )


def test_added_slot_cap_refuses_the_action_and_escalates(monkeypatch):
    state = _state(added_slots=policy.MAX_ADDED_SLOTS)
    monkeypatch.setattr(nodes, "_emit", lambda _event: None)

    updated = asyncio.run(nodes.apply(state, _config()))

    assert updated["escalation"]
    assert updated["escalation"]
    assert state["slots"] == _state()["slots"]
    assert nodes.after_apply({**state, **updated}) == "escalate"


def test_unknown_added_category_is_refused_and_falls_back_to_a_swap():
    board = Board(
        Requirements(),
        {"mcu": slot("mcu", parts.esp32s3(), pinned=False)},
        {"VIN": Rail("VIN", 5.0, None, ("mcu",))},
    )
    resolution = policy.Resolution(
        Verdict("voltage_overlap", "fail", "wrong supply", "mcu"), ("mcu",), False
    )
    proposal = reviewer.build_repair(
        {"slot": "mcu", "action": "add_part", "constraint": {"category": "flux_capacitor"}}
    )

    guarded = policy.enforce(proposal, resolution, board)

    assert not guarded.accepted
    assert guarded.repair.action == "swap"
    assert guarded.note == "missing or illegal part category"
