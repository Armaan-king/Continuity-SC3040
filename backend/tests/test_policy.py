"""Resolution policy — the fence, its ranking, and the guarantees around it.

These tests exist because this module is the security boundary of the whole design.
Everything the reviewer model can do to the board passes through `legal_set` and
`enforce`, so a hole here is a hole in the claim that no LLM decides compatibility.
"""

from __future__ import annotations

from dataclasses import replace

from continuity.engine import policy, rules
from continuity.engine.models import Board, Rail, Repair, Requirements, Verdict
from tests import parts
from tests.boards import usb_board


def conflicted_board(**kw):
    """The demo board at conflict 2: the OLED has pushed the 3V3 rail over the LDO."""
    return usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(), "sensor": parts.sht31(), "display": parts.oled()},
        **kw,
    )


def exhausted_board():
    """Every slot in the conflict has been repaired past the limit."""
    board = conflicted_board()
    return replace(
        board,
        slots={
            slot_id: replace(slot, repair_count=policy.MAX_REPAIRS + 1)
            for slot_id, slot in board.slots.items()
        },
    )


def current_conflict(board):
    failures = rules.failures(rules.evaluate(board))
    return next(v for v in failures if v.rule == "current_budget")


# ── the fence ─────────────────────────────────────────────────────────────────


def test_free_slots_rank_ahead_of_pinned_ones():
    """The part nobody asked for is reached for first, every time."""
    board = conflicted_board()
    conflict = current_conflict(board)

    legal = policy.legal_set(conflict, board, rules.passing(rules.evaluate(board)))

    assert set(conflict.involved) == {"regulator", "mcu", "sensor", "display"}
    assert legal[0] == "regulator"
    assert set(legal[1:]) == {"mcu", "sensor", "display"}


def test_a_pinned_slot_may_be_replaced_but_not_repurposed():
    """`pinned` locks the function, not the part — beat 4 of the demo depends on it."""
    board = conflicted_board()

    assert policy.allowed_actions(board, "sensor") == {"swap", "change_rail", "escalate"}
    assert "change_topology" in policy.allowed_actions(board, "regulator")
    assert "relax_requirement" not in policy.allowed_actions(board, "sensor")


def test_an_out_of_stock_pinned_part_is_repairable_not_escalated():
    """R6 involves only the failing slot. If pinned meant untouchable, this would stall."""
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(), "sensor": parts.sht40()},
    )
    conflict = next(
        v for v in rules.failures(rules.evaluate(board)) if v.rule == "availability"
    )

    resolution = policy.plan_resolution(conflict, board, rules.passing(rules.evaluate(board)))
    guarded = policy.enforce(
        Repair(slot="sensor", action="swap", rationale="Same bus, in stock."),
        resolution,
        board,
    )

    assert conflict.involved == ("sensor",)
    assert board.slots["sensor"].pinned
    assert not resolution.escalate
    assert resolution.legal == ("sensor",)
    assert guarded.accepted


def test_legal_set_is_ranked_least_disruptive_first():
    """A slot that six passing checks rest on is expensive to change; rank it last."""
    board = conflicted_board(pinned=())
    verdicts = rules.evaluate(board)
    conflict = current_conflict(board)

    legal = policy.legal_set(conflict, board, rules.passing(verdicts))
    costs = [policy.disruption(slot, rules.passing(verdicts), board) for slot in legal]

    assert costs == sorted(costs)
    assert legal[0] == "regulator", "the part nobody asked for is the cheapest to change"
    assert legal[-1] == "mcu", "the bus master every peripheral depends on is the last resort"


def test_ranking_is_stable_across_runs():
    board = conflicted_board(pinned=())
    verdicts = rules.evaluate(board)
    conflict = current_conflict(board)

    runs = {
        policy.legal_set(conflict, board, rules.passing(verdicts)) for _ in range(10)
    }

    assert len(runs) == 1


def test_a_slot_past_the_repair_limit_drops_out_of_the_fence():
    board = conflicted_board()
    board = replace(
        board,
        slots={
            **board.slots,
            "regulator": replace(board.slots["regulator"], repair_count=policy.MAX_REPAIRS + 1),
        },
    )

    legal = policy.legal_set(current_conflict(board), board, [])

    assert "regulator" not in legal
    assert legal, "the other participants are still available"


def test_the_fence_closes_once_every_participant_is_worn_out():
    assert policy.legal_set(current_conflict(exhausted_board()), exhausted_board(), []) == ()


# ── escalation ────────────────────────────────────────────────────────────────


def test_exhausted_repairs_escalate_with_a_reason_the_user_can_act_on():
    """"Swap it again" is the wrong question after four swaps. Say so."""
    board = exhausted_board()
    conflict = current_conflict(board)

    resolution = policy.plan_resolution(conflict, board, [])

    assert resolution.escalate
    assert "needs a requirement relaxed" in (resolution.reason or "")


def test_escalation_produces_an_escalate_repair_regardless_of_the_proposal():
    board = exhausted_board()
    resolution = policy.plan_resolution(current_conflict(board), board, [])
    proposal = Repair(slot="mcu", action="swap", rationale="swap the MCU")

    guarded = policy.enforce(proposal, resolution, board)

    assert guarded.repair.action == "escalate"
    assert guarded.repair.slot != "mcu"


# ── validation ────────────────────────────────────────────────────────────────


def build_resolution(board):
    verdicts = rules.evaluate(board)
    return policy.plan_resolution(current_conflict(board), board, rules.passing(verdicts))


def test_a_legal_proposal_is_applied_unchanged():
    board = conflicted_board()
    resolution = build_resolution(board)
    proposal = Repair(
        slot="regulator",
        action="change_topology",
        rationale="Any linear regulator burns (Vin−Vout)×I.",
    )

    guarded = policy.enforce(proposal, resolution, board)

    assert guarded.accepted
    assert guarded.repair is proposal


def test_repurposing_a_pinned_slot_is_rejected():
    """The model cannot widen its own fence, however confidently it argues."""
    board = conflicted_board()
    resolution = build_resolution(board)
    proposal = Repair(
        slot="mcu",
        action="relax_requirement",
        rationale="The user does not really need WiFi.",
    )

    guarded = policy.enforce(proposal, resolution, board)

    assert not guarded.accepted
    assert guarded.repair.slot == "regulator"
    assert guarded.repair.action == "swap"
    assert guarded.note.startswith("illegal action on pinned slot")
    assert "asked for by name" in guarded.repair.rationale


def test_a_proposal_naming_a_slot_that_does_not_exist_is_rejected():
    board = conflicted_board()
    resolution = build_resolution(board)

    guarded = policy.enforce(
        Repair(slot="voltage_supervisor", action="add_part", rationale="add a supervisor"),
        resolution,
        board,
    )

    assert not guarded.accepted
    assert guarded.repair.slot == "regulator"


def test_a_timeout_falls_back_to_minimum_disruption():
    board = conflicted_board()
    resolution = build_resolution(board)

    guarded = policy.enforce(None, resolution, board)

    assert not guarded.accepted
    assert guarded.repair.slot == resolution.legal[0]
    assert guarded.note == "reviewer timed out"


def test_the_fallback_says_out_loud_that_it_is_a_fallback():
    """A rationale that hides the fallback would be the one dishonest line on screen."""
    board = conflicted_board()

    guarded = policy.enforce(None, build_resolution(board), board)

    assert "Falling back" in guarded.repair.rationale
    assert "did not answer in time" in guarded.repair.rationale


def test_a_reviewer_may_still_choose_to_escalate():
    board = conflicted_board()
    proposal = Repair(slot="regulator", action="escalate", rationale="No part satisfies both.")

    guarded = policy.enforce(proposal, build_resolution(board), board)

    assert guarded.accepted
    assert guarded.repair.action == "escalate"


def rail_move_board(*, pinned: bool = False) -> Board:
    return Board(
        Requirements(),
        {
            "reg5": replace(conflicted_board().slots["regulator"], id="reg5", pinned=False),
            "reg3": replace(conflicted_board().slots["regulator"], id="reg3", pinned=False),
            "phy": replace(conflicted_board().slots["mcu"], id="phy", pinned=pinned),
        },
        {
            "5V0": Rail("5V0", 5.0, "reg5", ("phy",)),
            "3V3": Rail("3V3", 3.3, "reg3", ()),
        },
    )


def rail_move_resolution(board: Board, slot: str = "phy") -> policy.Resolution:
    return policy.Resolution(
        Verdict("voltage_overlap", "fail", "wrong rail", slot, (slot,)),
        (slot,),
        False,
    )


def test_change_rail_rejects_a_rail_that_is_not_on_the_board():
    board = rail_move_board()

    guarded = policy.enforce(
        Repair("phy", "change_rail", "Use 3.3 V.", {"rail": "1V8"}),
        rail_move_resolution(board),
        board,
    )

    assert not guarded.accepted
    assert guarded.repair.action == "swap"
    assert guarded.note == "illegal rail: 1V8"


def test_change_rail_rejects_a_missing_rail_target():
    board = rail_move_board()

    guarded = policy.enforce(
        Repair("phy", "change_rail", "Use a lower rail.", {}),
        rail_move_resolution(board),
        board,
    )

    assert not guarded.accepted
    assert guarded.repair.action == "swap"
    assert guarded.note == "missing rail"


def test_only_change_rail_may_carry_a_rail_target():
    board = rail_move_board()

    guarded = policy.enforce(
        Repair("phy", "swap", "Try another PHY.", {"rail": "3V3"}),
        rail_move_resolution(board),
        board,
    )

    assert not guarded.accepted
    assert guarded.repair.action == "swap"
    assert guarded.note == "rail target on swap"


def test_change_rail_rejects_a_source_less_input_rail():
    board = rail_move_board()
    board = replace(board, rails={**board.rails, "VIN": Rail("VIN", 5.0, None, ())})

    guarded = policy.enforce(
        Repair("phy", "change_rail", "Use the input rail.", {"rail": "VIN"}),
        rail_move_resolution(board),
        board,
    )

    assert not guarded.accepted
    assert guarded.repair.action == "swap"
    assert guarded.note == "source-less rail: VIN"


def test_change_rail_rejects_the_rail_the_slot_is_already_on():
    board = rail_move_board()

    guarded = policy.enforce(
        Repair("phy", "change_rail", "Leave it on 5 V.", {"rail": "5V0"}),
        rail_move_resolution(board),
        board,
    )

    assert not guarded.accepted
    assert guarded.repair.action == "swap"
    assert guarded.note == "unchanged rail: 5V0"


def test_change_rail_cannot_move_a_rail_source():
    board = rail_move_board()

    guarded = policy.enforce(
        Repair("reg3", "change_rail", "Move the regulator.", {"rail": "5V0"}),
        rail_move_resolution(board, "reg3"),
        board,
    )

    assert not guarded.accepted
    assert guarded.repair.action == "swap"
    assert guarded.note == "rail source: reg3"


def test_a_pinned_slot_may_change_rail():
    board = rail_move_board(pinned=True)
    proposal = Repair("phy", "change_rail", "It belongs on 3.3 V.", {"rail": "3V3"})

    guarded = policy.enforce(proposal, rail_move_resolution(board), board)

    assert guarded.accepted
    assert guarded.repair is proposal


# ── termination ───────────────────────────────────────────────────────────────


def test_repair_counting_does_not_mutate_the_board():
    board = conflicted_board()

    after = policy.register_repair(board, "regulator")

    assert board.slots["regulator"].repair_count == 0
    assert after.slots["regulator"].repair_count == 1


def test_the_loop_cannot_spin():
    """A conflict that never resolves still terminates — in escalation, bounded.

    The bound is per slot, not per conflict, so the fence drains one participant at a
    time. What matters is that it drains: an adversarial reviewer that keeps proposing
    repairs which do not fix anything cannot keep the graph running forever.
    """
    board = conflicted_board()
    conflict = current_conflict(board)
    ceiling = len(board.slots) * (policy.MAX_REPAIRS + 1) + 1

    for iteration in range(ceiling):
        resolution = policy.plan_resolution(conflict, board, [])
        if resolution.escalate:
            assert resolution.reason
            break
        board = policy.register_repair(board, resolution.legal[0])
    else:
        raise AssertionError(f"still repairing after {ceiling} rounds")

    assert iteration <= ceiling


def test_registering_a_repair_on_an_unknown_slot_is_a_no_op():
    board = conflicted_board()

    assert policy.register_repair(board, "nonexistent") is board


# ── the invariant ─────────────────────────────────────────────────────────────


def test_no_proposal_can_change_what_a_pinned_slot_is_for():
    """Sweep the action space: a pinned slot only ever gets an equivalent part."""
    board = conflicted_board()
    resolution = build_resolution(board)
    pinned = {slot_id for slot_id, slot in board.slots.items() if slot.pinned}

    proposals = [None] + [
        Repair(slot=target, action=action, rationale="…")
        for target in [*board.slots, "hallucinated_slot"]
        for action in ("swap", "change_topology", "add_part", "change_rail", "relax_requirement")
    ]

    for proposal in proposals:
        applied = policy.enforce(proposal, resolution, board).repair
        if applied.slot in pinned:
            assert applied.action in policy.PINNED_ACTIONS, proposal
