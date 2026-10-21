"""What the reviewer may decide, and what the fence does with the rest.

This is the last place a model touches the board and the most constrained one. The
engine has already proved the fault; the reviewer only chooses the response.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from continuity import reviewer
from continuity.engine import policy, rules
from continuity.engine.models import Repair
from tests import parts
from tests.boards import usb_board


def conflicted():
    board = usb_board(
        regulator=parts.ldo_1a(),
        loads={"mcu": parts.esp32s3(), "sensor": parts.sht31(), "display": parts.oled()},
    )
    conflict = next(
        v for v in rules.failures(rules.evaluate(board)) if v.rule == "thermal_dissipation"
    )
    verdicts = rules.evaluate(board)
    return board, conflict, policy.plan_resolution(conflict, board, rules.passing(verdicts))


# ── the reply schema ──────────────────────────────────────────────────────────


def test_a_well_formed_repair_survives():
    repair = reviewer.build_repair(
        {
            "slot": "regulator",
            "action": "change_topology",
            "rationale": "A linear regulator burns (Vin-Vout)xI however large it is.",
            "constraint": {"topology": "buck", "i_out_min": 1.0, "efficiency_min": 0.9},
        }
    )

    assert repair.slot == "regulator"
    assert repair.action == "change_topology"
    assert repair.constraint == {"topology": "buck", "i_out_min": 1.0, "efficiency_min": 0.9}


def test_an_action_outside_the_enum_is_not_a_repair():
    assert reviewer.build_repair({"slot": "regulator", "action": "ignore_it"}) is None
    assert reviewer.build_repair({"slot": "regulator", "action": "override"}) is None


def test_a_repair_naming_no_slot_is_not_a_repair():
    assert reviewer.build_repair({"action": "swap"}) is None


def test_an_invented_constraint_key_is_dropped():
    """`sourcing` knows how to push down five keys; anything else would be silently
    ignored later, so it is dropped here where the reason is visible."""
    repair = reviewer.build_repair(
        {
            "slot": "regulator",
            "action": "swap",
            "constraint": {"topology": "buck", "must_be_blue": True, "vendor": "TI"},
        }
    )

    assert repair.constraint == {"topology": "buck"}


def test_a_rail_target_is_preserved_for_change_rail():
    repair = reviewer.build_repair(
        {"slot": "mcu", "action": "change_rail", "constraint": {"rail": "3V3"}}
    )

    assert repair.constraint == {"rail": "3V3"}


def test_a_mistyped_constraint_value_is_dropped():
    repair = reviewer.build_repair(
        {"slot": "r", "action": "swap", "constraint": {"i_out_min": "one amp"}}
    )

    assert repair.constraint == {}


def test_a_temperature_constraint_is_validated_as_a_number():
    accepted = reviewer.build_repair(
        {"slot": "display", "action": "swap", "constraint": {"rated_to": 85}}
    )
    rejected = reviewer.build_repair(
        {"slot": "display", "action": "swap", "constraint": {"rated_to": "hot"}}
    )

    assert accepted.constraint == {"rated_to": 85.0}
    assert rejected.constraint == {}


def test_a_missing_rationale_still_produces_something_readable():
    assert reviewer.build_repair({"slot": "regulator", "action": "swap"}).rationale


def test_an_essay_is_truncated():
    repair = reviewer.build_repair(
        {"slot": "r", "action": "swap", "rationale": "x" * 5000}
    )

    assert len(repair.rationale) <= reviewer.MAX_RATIONALE


# ── the fence ─────────────────────────────────────────────────────────────────


def test_the_reviewer_cannot_reach_a_slot_outside_the_legal_set():
    board, _, resolution = conflicted()

    guarded = policy.enforce(
        reviewer.build_repair({"slot": "hallucinated", "action": "swap"}), resolution, board
    )

    assert not guarded.accepted
    assert guarded.repair.slot in resolution.legal


def test_the_reviewer_cannot_repurpose_a_pinned_slot():
    board, _, resolution = conflicted()

    guarded = policy.enforce(
        reviewer.build_repair(
            {"slot": "display", "action": "change_topology", "rationale": "use e-paper"}
        ),
        resolution,
        board,
    )

    assert not guarded.accepted
    assert guarded.repair.slot == "regulator"


def test_a_legal_topology_change_is_applied_untouched():
    board, _, resolution = conflicted()
    proposal = reviewer.build_repair(
        {"slot": "regulator", "action": "change_topology", "constraint": {"topology": "buck"}}
    )

    guarded = policy.enforce(proposal, resolution, board)

    assert guarded.accepted
    assert guarded.repair.action == "change_topology"


def test_no_model_means_the_fallback_not_a_crash():
    """`propose` returning None is indistinguishable from a timeout, by design."""
    board, _, resolution = conflicted()

    guarded = policy.enforce(None, resolution, board)

    assert guarded.repair.slot == resolution.legal[0]
    assert guarded.repair.action == "swap"


# ── what it is shown ──────────────────────────────────────────────────────────


def test_the_prompt_shows_only_slots_it_may_change():
    board, conflict, resolution = conflicted()

    described = reviewer._describe(board, conflict, resolution, {})

    assert "regulator" in described
    for slot_id, slot in board.slots.items():
        if slot_id not in resolution.legal:
            assert f'"id": "{slot_id}"' not in described


def test_the_prompt_carries_the_finding_and_its_evidence():
    board, conflict, resolution = conflicted()

    described = reviewer._describe(board, conflict, resolution, {})

    assert conflict.detail in described
    assert "SOT-23-5" in described, "the package that caused the failure"


def test_the_prompt_states_which_actions_each_slot_permits():
    board, conflict, resolution = conflicted()
    board = replace(
        board, slots={**board.slots, "regulator": replace(board.slots["regulator"], pinned=True)}
    )

    described = reviewer._describe(board, conflict, resolution, {})

    assert '"pinned": true' in described
    assert "change_topology" not in described.split('"allowed_actions"')[1][:120]


def test_add_part_requires_an_engine_owned_category():
    repair = reviewer.build_repair(
        {"slot": "regulator", "action": "add_part", "constraint": {"category": "regulator"}}
    )

    assert repair is not None
    assert repair.constraint == {"category": "regulator"}
    assert reviewer.build_repair({"slot": "regulator", "action": "relax_requirement"}) is None


def test_the_prompt_only_offers_implemented_actions():
    assert "change_rail" in reviewer.SYSTEM
    assert "add_part" in reviewer.SYSTEM
    for action in reviewer.ACTIONS:
        assert action in reviewer.SYSTEM


def test_the_reviewer_is_given_existing_rail_ids_for_a_rail_move():
    board, conflict, resolution = conflicted()

    described = json.loads(reviewer._describe(board, conflict, resolution, {}))

    assert {rail["id"] for rail in described["board_rails"]} == {
        rail.id for rail in board.rails.values() if rail.source is not None
    }


def test_a_named_replacement_is_a_valid_constraint():
    """The reviewer sees the candidates; naming one is instant and certain, where a
    re-search cannot express "must tolerate 5 V" and returns the same failing part."""
    repair = reviewer.build_repair(
        {"slot": "mcu", "action": "swap", "constraint": {"mpn": "STM8S003F3P6TR"}}
    )

    assert repair.constraint == {"mpn": "stm8s003f3p6tr"}


def test_the_prompt_tells_the_reviewer_to_look_at_the_candidates_first():
    assert "replacements_available" in reviewer.SYSTEM
    assert "constraint.mpn" in reviewer.SYSTEM


# ── guidance from an escalation ───────────────────────────────────────────────


def test_guidance_reaches_the_prompt():
    """What the user typed at an escalation must inform the next attempt.

    Storing it and not reading it would be the same "echo and discard" the escalation
    node was fixed for, one layer down.
    """
    board = usb_board(regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3()})
    conflict = rules.failures(rules.evaluate(board)) or rules.evaluate(board)
    verdict = conflict[0]
    resolution = policy.plan_resolution(verdict, board, [])

    described = reviewer._describe(
        board, verdict, resolution, {}, guidance="use a buck converter instead"
    )

    assert "use a buck converter instead" in described


def test_no_guidance_changes_nothing():
    board = usb_board(regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3()})
    verdict = rules.evaluate(board)[0]
    resolution = policy.plan_resolution(verdict, board, [])

    assert reviewer._describe(board, verdict, resolution, {}) == reviewer._describe(
        board, verdict, resolution, {}, guidance=None
    )


def test_precedents_are_omitted_without_a_precedent():
    board, conflict, resolution = conflicted()

    assert '"precedents"' not in reviewer._describe(board, conflict, resolution, {})


def test_precedents_supply_only_a_situation_and_action():
    board, conflict, resolution = conflicted()
    described = reviewer._describe(
        board,
        conflict,
        resolution,
        {},
        precedents=(
            {
                "signature": "thermal_dissipation|regulator|linear|pkg:SOT|drop:>=8V|load:100-500mA",
                "action": "change_topology",
                "replacement_mpn": "NEVER-SHOW-THIS-MPN",
            },
        ),
    )

    assert '"precedents"' in described
    assert "NEVER-SHOW-THIS-MPN" not in described


# ── the reviewer must know what a regulator has to produce ────────────────────
#
# Soil-logger run, 9 Aug. The reviewer reached: "TPS61040DBVR can operate from 3 V but
# is a boost converter, which only helps if the output voltage is above 3 V — the
# required output voltage is unknown, so the user must clarify."
#
# It is 3.3 V. The rail says so. The reviewer was shown the part's own vout and never
# the rail it was chosen to make, so it escalated one fact short of the answer — and the
# answer was a boost, which is what this brief needs.


def _regulator_conflict():
    board = usb_board(
        regulator=parts.ap2112k(vout_min=5.0, vout_max=5.0, topology="buck"),
        loads={"mcu": parts.esp32s3()},
        rail_voltage=3.3,
        input_voltage=3.0,
        pinned=(),
    )
    conflict = next(
        v for v in rules.voltage_overlap(board)
        if v.status == "fail" and v.subject == "regulator"
    )
    return board, conflict, policy.plan_resolution(conflict, board, [])


def test_the_reviewer_is_told_which_rail_a_slot_must_supply():
    board, conflict, resolution = _regulator_conflict()

    described = reviewer._describe(board, conflict, resolution, {})

    assert "3.3" in described, "the rail voltage the regulator must produce"


def test_the_reviewer_is_told_what_feeds_the_slot():
    board, conflict, resolution = _regulator_conflict()

    described = reviewer._describe(board, conflict, resolution, {})

    assert "3.0" in described or "3 " in described, "the input voltage available to it"


def test_an_adjustable_parts_range_is_visible_not_just_its_setpoint():
    """`vout` is null for an adjustable part, so the range has to be shown separately."""
    board = usb_board(
        regulator=parts.ap2112k(vout_min=1.2, vout_max=37.0, topology="buck"),
        loads={"mcu": parts.esp32s3()},
        pinned=(),
    )
    conflict = rules.evaluate(board)[0]
    resolution = policy.plan_resolution(conflict, board, [])

    described = reviewer._describe(board, conflict, resolution, {})

    assert "37" in described
