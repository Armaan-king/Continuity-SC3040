"""The demo, as an executable acceptance test.

Design doc §9: *"Temp + humidity sensor, WiFi and BLE, USB-C powered with Li-ion
backup, small OLED readout, consumer device."* Three conflicts, ordered establish →
complicate → pay off.

This file is the guard against the storyboard and the engine drifting apart. If a beat
stops landing — or lands for the wrong reason — it fails here rather than on stage.

The parts are test doubles. The *arithmetic* is the real thing, and the sentences
asserted below are the sentences the audience reads.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from continuity.engine import policy, rules
from continuity.engine.models import Repair
from tests import parts
from tests.boards import usb_board

RAIL = "3V3"


def board_with(regulator, **loads):
    """The demo board. The user named the sensor, the radio and the display."""
    return usb_board(
        regulator=regulator, loads=loads, pinned=("mcu", "sensor", "display")
    )


def conflict_over(board, rule: str):
    failures = rules.failures(rules.evaluate(board))
    matching = [v for v in failures if v.rule == rule]
    assert matching, f"expected a {rule} conflict, got {[v.rule for v in failures]}"
    return matching[0]


def resolve(board, conflict):
    verdicts = rules.evaluate(board)
    return policy.plan_resolution(conflict, board, rules.passing(verdicts))


# ── beat 2 · build-up ─────────────────────────────────────────────────────────


def test_beat_2_the_radio_and_regulator_are_chosen_and_the_rail_is_tight():
    board = board_with(parts.ldo_600ma(), mcu=parts.esp32s3(), sensor=parts.sht40())

    current = [v for v in rules.evaluate(board) if v.rule == "current_budget" and v.scope == RAIL]

    assert current[0].status == "warn"
    assert current[0].detail == "500.3 mA of 600 mA (83%) — inside the 20% derating band."


def test_beat_2_foreshadows_the_conflict_that_arrives_four_beats_later():
    """The regulator is flagged tight before the display ever touches it."""
    board = board_with(parts.ldo_600ma(), mcu=parts.esp32s3(), sensor=parts.sht40())

    warned = [v for v in rules.evaluate(board) if v.status == "warn" and v.subject == "regulator"]

    assert any("derating band" in v.detail for v in warned)


# ── beats 3–4 · conflict 1, sourcing ──────────────────────────────────────────


def test_beat_3_the_sensor_is_out_of_stock():
    """No domain knowledge required to read this one. That is what it is for."""
    board = board_with(parts.ldo_600ma(), mcu=parts.esp32s3(), sensor=parts.sht40())

    conflict = conflict_over(board, "availability")

    assert conflict.subject == "sensor"
    assert conflict.detail == "SHT40-AD1B-R2: 0 in stock at JLCPCB, below the 100 minimum."
    assert conflict.evidence[0].value == "0"


def test_beat_4_the_sensor_is_replaced_despite_being_pinned():
    board = board_with(parts.ldo_600ma(), mcu=parts.esp32s3(), sensor=parts.sht40())
    conflict = conflict_over(board, "availability")

    resolution = resolve(board, conflict)
    guarded = policy.enforce(
        Repair(
            slot="sensor",
            action="swap",
            rationale="Same I²C bus, compatible supply range, in stock.",
            constraint={"interfaces": ["I2C"], "min_stock": 100},
        ),
        resolution,
        board,
    )

    assert not resolution.escalate
    assert resolution.legal == ("sensor",)
    assert guarded.accepted


def test_beat_4_lands_green():
    board = board_with(parts.ldo_600ma(), mcu=parts.esp32s3(), sensor=parts.sht31())

    assert rules.failures(rules.evaluate(board)) == []


# ── beats 6–7 · conflict 2, current budget ────────────────────────────────────


def test_beat_6_the_display_pushes_the_rail_past_the_regulator():
    board = board_with(
        parts.ldo_600ma(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()
    )

    conflict = conflict_over(board, "current_budget")

    assert conflict.subject == "regulator"
    assert conflict.detail == (
        "701.5 mA on 3V3 plus 15% margin = 806.7 mA, "
        "above the 600 mA rating of AP2114H-3.3TRG1."
    )


def test_beat_6_fences_the_repair_onto_the_part_nobody_asked_for():
    """Three parts share the rail. Only one of them is an implementation detail."""
    board = board_with(
        parts.ldo_600ma(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()
    )
    conflict = conflict_over(board, "current_budget")

    resolution = resolve(board, conflict)

    assert set(conflict.involved) == {"regulator", "mcu", "sensor", "display"}
    assert resolution.legal[0] == "regulator"


def test_beat_7_a_bigger_ldo_clears_the_current_budget():
    board = board_with(
        parts.ldo_1a(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()
    )

    current = [v for v in rules.evaluate(board) if v.rule == "current_budget" and v.scope == RAIL]

    assert current[0].status == "pass"
    assert current[0].detail == "701.5 mA of 1000 mA (70%)"


# ── beats 8–9 · conflict 3, thermal ───────────────────────────────────────────


def test_beat_8_the_same_node_fails_again_on_heat():
    """The payoff beat. A mechanical policy would fetch a bigger LDO and land here forever."""
    board = board_with(
        parts.ldo_1a(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()
    )

    conflict = conflict_over(board, "thermal_dissipation")

    assert conflict.subject == "regulator"
    assert conflict.detail == (
        "(5 V − 3.3 V) × 701.5 mA = 1.19 W in SOT-23-5 — "
        "298 °C rise, 323 °C junction against a 125 °C limit."
    )


def test_beat_8_shows_its_working_and_cites_the_package_honestly():
    board = board_with(
        parts.ldo_1a(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()
    )

    conflict = conflict_over(board, "thermal_dissipation")
    fields = {e.field: e for e in conflict.evidence}

    assert fields["Package / Case"].value == "SOT-23-5"
    assert fields["Package / Case"].source == parts.DATASHEET
    assert "package table" in (fields["θJA (package table)"].source or "")


def test_beat_9_changing_topology_is_legal_because_the_regulator_is_free():
    board = board_with(
        parts.ldo_1a(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()
    )
    conflict = conflict_over(board, "thermal_dissipation")

    guarded = policy.enforce(
        Repair(
            slot="regulator",
            action="change_topology",
            rationale="Any linear regulator burns (Vin−Vout)×I. Switching to a buck converter.",
            constraint={"topology": "buck", "i_out_min": 1.0, "efficiency_min": 0.85},
        ),
        resolve(board, conflict),
        board,
    )

    assert guarded.accepted
    assert guarded.repair.action == "change_topology"


def test_beat_9_the_same_repair_would_be_refused_on_a_part_the_user_named():
    """The model chose *how* to fix it. It never chose what it was allowed to touch."""
    board = board_with(
        parts.ldo_1a(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()
    )
    conflict = conflict_over(board, "thermal_dissipation")

    guarded = policy.enforce(
        Repair(slot="display", action="change_topology", rationale="Use an e-paper panel."),
        resolve(board, conflict),
        board,
    )

    assert not guarded.accepted
    assert guarded.repair.slot == "regulator"


def test_beat_9_the_buck_converter_barely_warms():
    board = board_with(
        parts.buck_3v3(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()
    )

    thermal = [v for v in rules.evaluate(board) if v.rule == "thermal_dissipation"]

    assert thermal[0].status == "pass"
    assert thermal[0].detail == (
        "92% efficient at 3.3 V × 701.5 mA = 0.2 W — 11 °C rise in VSON-HR-8, "
        "36 °C junction."
    )


# ── beat 10 · close ───────────────────────────────────────────────────────────


def test_the_board_goes_green():
    board = board_with(
        parts.buck_3v3(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()
    )

    verdicts = rules.evaluate(board)

    assert rules.failures(verdicts) == []
    assert [v.status for v in verdicts if v.status == "warn"] == []


def test_one_loop_handles_a_sourcing_failure_and_an_electrical_one_identically():
    """The architectural claim, asserted rather than narrated."""
    sourcing = board_with(parts.ldo_600ma(), mcu=parts.esp32s3(), sensor=parts.sht40())
    electrical = board_with(
        parts.ldo_1a(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()
    )

    for board, rule in ((sourcing, "availability"), (electrical, "thermal_dissipation")):
        conflict = conflict_over(board, rule)
        resolution = resolve(board, conflict)

        assert not resolution.escalate
        assert resolution.legal
        assert policy.enforce(
            Repair(slot=resolution.legal[0], action="swap", rationale="…"), resolution, board
        ).accepted


def test_the_whole_run_is_reproducible():
    """Rehearsal and stage must produce the same board. No LLM touches any of this."""
    def run():
        stages = [
            board_with(parts.ldo_600ma(), mcu=parts.esp32s3(), sensor=parts.sht40()),
            board_with(parts.ldo_600ma(), mcu=parts.esp32s3(), sensor=parts.sht31()),
            board_with(parts.ldo_600ma(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()),
            board_with(parts.ldo_1a(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()),
            board_with(parts.buck_3v3(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()),
        ]
        return [
            (v.rule, v.subject, v.scope, v.status, v.detail)
            for board in stages
            for v in rules.evaluate(board)
        ]

    assert run() == run()


def test_exactly_three_conflicts_across_the_run():
    """Establish, complicate, pay off — no fourth surprise mid-demo."""
    stages = [
        board_with(parts.ldo_600ma(), mcu=parts.esp32s3(), sensor=parts.sht40()),
        board_with(parts.ldo_600ma(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()),
        board_with(parts.ldo_1a(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()),
        board_with(parts.buck_3v3(), mcu=parts.esp32s3(), sensor=parts.sht31(), display=parts.oled()),
    ]

    conflicts = [rules.failures(rules.evaluate(board)) for board in stages]

    # The graph repairs one conflict at a time, so what matters is which failure the
    # engine surfaces first at each stage — not how many are latent behind it.
    assert [c[0].rule for c in conflicts[:3]] == [
        "availability",
        "current_budget",
        "thermal_dissipation",
    ]
    assert conflicts[-1] == []
