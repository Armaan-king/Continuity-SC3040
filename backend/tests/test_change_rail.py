"""Rail moves are local topology repairs: no part replacement or search occurs."""

from __future__ import annotations

import asyncio

from continuity.api.events import EventStream
from continuity.engine.models import Rail, Requirements
from continuity.graph import nodes, sourcing
from tests import parts
from tests.boards import slot


def test_change_rail_moves_a_part_without_searching_or_normalising(monkeypatch):
    part = parts.esp32s3()
    slots = {
        "reg5": slot("reg5", parts.ldo_600ma(), tier="power"),
        "reg3": slot("reg3", parts.ldo_600ma(), tier="power"),
        "phy": slot("phy", part, label="Ethernet PHY"),
    }
    rails = {
        "5V0": Rail("5V0", 5.0, "reg5", ("phy",)),
        "3V3": Rail("3V3", 3.3, "reg3", ()),
    }
    emitted = []

    async def unexpected(*args, **kwargs):
        raise AssertionError("a rail move must not search or normalise a part")

    monkeypatch.setattr(nodes, "_emit", emitted.append)
    monkeypatch.setattr(sourcing, "find", unexpected)
    monkeypatch.setattr(sourcing, "choose", unexpected)
    state = {
        "requirements": Requirements(),
        "slots": slots,
        "rails": rails,
        "current": "phy",
        "constraint": {"rail": "3V3"},
        "candidates": {},
        "cursor": {},
        "conflicts_resolved": 4,
    }

    updated = asyncio.run(
        nodes.apply(state, {"configurable": {"events": EventStream("change-rail")}})
    )

    assert updated["rails"]["5V0"].members == ()
    assert updated["rails"]["3V3"].members == ("phy",)
    assert updated["slots"]["phy"].part is part
    assert updated["slots"]["phy"].repair_count == 1
    assert updated["conflicts_resolved"] == 5
    assert updated["constraint"] is None
    assert any(
        "5V0" in event["text"] and "3V3" in event["text"]
        for event in emitted
        if event["type"] == "reasoning"
    )
    edge_event = next(event for event in emitted if event["type"] == "selection")
    assert edge_event["edges"] == [
        {"id": "pwr-phy", "from": "reg3", "label": "3V3", "status": "pending"}
    ]


def test_after_apply_returns_to_validate_after_a_rail_move():
    assert nodes.after_apply({}) == "validate"
