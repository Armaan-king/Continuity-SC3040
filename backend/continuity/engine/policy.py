"""Resolution policy — the fence the reviewer model is allowed to move inside.

The engine decides what is broken. The model decides what to do about it. This module
is the seam between those two sentences, and it does three jobs:

1. **Fence.** Compute the legal set: which slots participating in a conflict may be
   changed at all, ranked so the least disruptive comes first.
2. **Validate.** Check what the model chose against that set before anything is
   applied, falling back to minimum disruption when the choice is illegal or absent.
3. **Terminate.** Guarantee the loop cannot spin — bounded repairs per slot, and
   escalation to the user when the fence encloses nothing.

None of this asks a model anything. A reviewer that returns garbage, times out, or is
prompt-injected through a distributor description cannot widen its own fence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from .models import Board, Repair, RepairAction, Verdict
from ..parts import categories

# The one place the engine reaches outside itself, and it is deliberate: `categories` is a
# pure vocabulary — a dict and two functions, no I/O, no network, no LLM, and it imports
# nothing from `engine`, so there is no cycle. `enforce` is the fence, and a fence that
# cannot see the vocabulary it enforces is not one. `engine/situation.py` takes the other
# route — the caller resolves the category and passes it in — because that path needed a
# distributor string *canonicalised*, which is genuine `parts` domain knowledge; this one
# only asks whether a name is in a fixed set.

MAX_REPAIRS = 3
"""Repairs a single slot may absorb before the conflict goes to the user instead."""

MAX_ADDED_SLOTS = 3
"""Late slots permitted in one run before the board is escalated to the user."""

PINNED_ACTIONS = frozenset({"swap", "change_rail", "escalate"})
"""What may be done to a slot the user named.

`pinned` locks the *function*, not the part. The user asking for a temperature sensor
means the board must have one — it does not mean the first sensor we happened to pick
is now untouchable. Replacing a sold-out SHT40 with an SHT31 keeps the promise; the
user still gets their sensor, on the same bus, at the same supply.

Everything else is forbidden on a pinned slot, because everything else changes what
was asked for rather than how it is met: `relax_requirement` drops it and
`change_topology` makes it a different kind of part. `change_rail` is allowed because
moving the named part to the rail it should have been on changes neither its identity
nor its function.
"""


@dataclass(frozen=True)
class Resolution:
    """What the engine will permit in response to one conflict."""

    conflict: Verdict
    legal: tuple[str, ...]
    """Slots the reviewer may act on, least disruptive first. Empty means escalate."""

    escalate: bool
    reason: str | None = None
    """Why we are escalating. Shown to the user verbatim, so it explains the trade-off."""


# ── 1 · fence ─────────────────────────────────────────────────────────────────


def disruption(slot_id: str, passing: Sequence[Verdict], board: Board) -> int:
    """How many already-satisfied checks a like-for-like replacement would invalidate.

    Not simply "checks this slot appears in". Appearing in a check as a *bystander*
    only costs something if the dependency would actually break:

    - The controller is a bystander on every peripheral's `interface_role_match`, and
      that dependency is real — swap the MCU and the peripherals may lose their bus.
    - The regulator is a bystander on every `voltage_overlap` for parts on the rail it
      feeds, and that dependency is *not* real under a swap: replace a 3.3 V regulator
      with another 3.3 V regulator and every part on 3V3 is checked against the same
      voltage it was before. Only a `change_rail` would invalidate those, and a repair
      that changes the rail re-runs them anyway.

    Counting the second kind is not a rounding error, it inverts the ranking. A rail
    source is a bystander once per part it feeds, so on any real board it accumulates
    the largest count and sorts *last* — the policy would reach for the microcontroller
    before the regulator nobody asked for, which is the opposite of the intent.
    """
    total = 0
    for verdict in passing:
        if slot_id not in verdict.involved:
            continue
        if verdict.subject == slot_id:
            total += 1
            continue
        rail = board.rails.get(verdict.scope) if verdict.scope else None
        if rail is not None and rail.source == slot_id:
            continue  # survives a like-for-like swap; the rail voltage is preserved
        total += 1
    return total


def allowed_actions(board: Board, slot_id: str) -> frozenset[str]:
    """What the reviewer may do to one slot. See `PINNED_ACTIONS`."""
    slot = board.slots.get(slot_id)
    if slot is None:
        return frozenset()
    return PINNED_ACTIONS if slot.pinned else frozenset(RepairAction.__args__)


def legal_set(conflict: Verdict, board: Board, passing: Sequence[Verdict]) -> tuple[str, ...]:
    """The slots that may be changed to resolve `conflict`, cheapest first.

    Excluded only: slots repaired past `MAX_REPAIRS`. Pinned slots stay in — what they
    restrict is the *action*, not the access, or an out-of-stock part the user named
    could never be replaced and every sourcing conflict would land on the user.

    Ranked by `pinned` first, then disruption. Free slots always sort ahead of pinned
    ones, so the minimum-disruption fallback reaches for the regulator nobody asked
    for before it reaches for the sensor somebody did. Ties keep the order they appear
    in `conflict.involved` — Python's sort is stable, so the same conflict yields the
    same fence every run, which is what makes a rehearsed demo stay rehearsed.
    """
    candidates = [
        slot_id
        for slot_id in conflict.involved
        if slot_id in board.slots and board.slots[slot_id].repair_count <= MAX_REPAIRS
    ]
    return tuple(
        sorted(
            candidates,
            key=lambda slot_id: (
                board.slots[slot_id].pinned,
                disruption(slot_id, passing, board),
            ),
        )
    )


def plan_resolution(
    conflict: Verdict, board: Board, passing: Sequence[Verdict]
) -> Resolution:
    """Fence the conflict, or decide it has to go back to the user."""
    legal = legal_set(conflict, board, passing)
    if legal:
        return Resolution(conflict=conflict, legal=legal, escalate=False)
    return Resolution(
        conflict=conflict,
        legal=(),
        escalate=True,
        reason=_escalation_reason(conflict, board),
    )


def _escalation_reason(conflict: Verdict, board: Board) -> str:
    """Say which way the fence closed, in terms the user can actually answer."""
    worn_out = [
        slot_id
        for slot_id in conflict.involved
        if slot_id in board.slots and board.slots[slot_id].repair_count > MAX_REPAIRS
    ]
    if worn_out:
        labels = ", ".join(board.slots[slot_id].label for slot_id in worn_out)
        return (
            f"{labels} has been replaced {MAX_REPAIRS + 1} times and still fails "
            f"{conflict.rule.replace('_', ' ')}. This needs a requirement relaxed, "
            f"not another part. {conflict.detail}"
        )
    return (
        f"Nothing in this conflict is still available to change. {conflict.detail}"
    )


# ── 2 · validate ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Guarded:
    """A repair that has been checked against the fence, and whether it survived intact."""

    repair: Repair
    accepted: bool
    """False when the reviewer's choice was rejected and the fallback was substituted."""

    note: str | None = None


def enforce(proposal: Repair | None, resolution: Resolution, board: Board) -> Guarded:
    """Validate the reviewer's decision against the legal set before anything is applied.

    An illegal target, an unparseable response or a timeout all land in the same place:
    the minimum-disruption swap on the first legal slot. The model can only ever choose
    *how* to fix something the engine already proved was broken, never *whether* it was
    broken and never *what it is allowed to touch*.
    """
    if resolution.escalate or not resolution.legal:
        return Guarded(
            repair=Repair(
                slot=resolution.conflict.subject,
                action="escalate",
                rationale=resolution.reason or resolution.conflict.detail,
            ),
            accepted=True,
        )

    if proposal is None:
        return Guarded(
            repair=_fallback(resolution, board, "the reviewer did not answer in time"),
            accepted=False,
            note="reviewer timed out",
        )

    if proposal.action == "escalate":
        return Guarded(repair=proposal, accepted=True)

    if proposal.slot not in resolution.legal:
        return Guarded(
            repair=_fallback(
                resolution,
                board,
                f"the reviewer proposed changing {proposal.slot}, which is not "
                f"available to change",
            ),
            accepted=False,
            note=f"illegal target: {proposal.slot}",
        )

    if proposal.action != "change_rail" and "rail" in proposal.constraint:
        return _rejected_rail(
            resolution, board, f"rail target on {proposal.action}"
        )

    permitted = allowed_actions(board, proposal.slot)
    if proposal.action not in permitted:
        label = board.slots[proposal.slot].label
        return Guarded(
            repair=_fallback(
                resolution,
                board,
                f"the reviewer proposed to {proposal.action.replace('_', ' ')} the "
                f"{label}, which you asked for by name — it can be replaced with an "
                f"equivalent part, but not changed into something else",
            ),
            accepted=False,
            note=f"illegal action on pinned slot: {proposal.action} on {proposal.slot}",
        )

    if proposal.action == "change_rail":
        target = proposal.constraint.get("rail")
        if not isinstance(target, str) or not target:
            return _rejected_rail(resolution, board, "missing rail")
        if target not in board.rails:
            return _rejected_rail(resolution, board, f"illegal rail: {target}")
        if board.rails[target].source is None:
            return _rejected_rail(resolution, board, f"source-less rail: {target}")
        if any(rail.source == proposal.slot for rail in board.rails.values()):
            return _rejected_rail(resolution, board, f"rail source: {proposal.slot}")
        if proposal.slot in board.rails[target].members:
            return _rejected_rail(resolution, board, f"unchanged rail: {target}")

    if proposal.action == "add_part":
        category = proposal.constraint.get("category")
        if set(proposal.constraint) != {"category"} or category not in categories.CATEGORIES:
            return Guarded(
                repair=_fallback(resolution, board, "missing or illegal part category"),
                accepted=False,
                note="missing or illegal part category",
            )

    return Guarded(repair=proposal, accepted=True)


def _rejected_rail(resolution: Resolution, board: Board, note: str) -> Guarded:
    return Guarded(repair=_fallback(resolution, board, note), accepted=False, note=note)


def _fallback(resolution: Resolution, board: Board, because: str) -> Repair:
    """Minimum disruption: swap the cheapest legal slot, and say why we are here."""
    slot_id = resolution.legal[0]
    label = board.slots[slot_id].label if slot_id in board.slots else slot_id
    return Repair(
        slot=slot_id,
        action="swap",
        rationale=(
            f"Falling back to the least disruptive fix — replacing the {label} — "
            f"because {because}."
        ),
    )


# ── 3 · terminate ─────────────────────────────────────────────────────────────


def register_repair(board: Board, slot_id: str) -> Board:
    """Count a repair against a slot. Returns a new board; nothing is mutated."""
    slot = board.slots.get(slot_id)
    if slot is None:
        return board
    slots = dict(board.slots)
    slots[slot_id] = replace(slot, repair_count=slot.repair_count + 1)
    return replace(board, slots=slots)


def exhausted(board: Board, slot_id: str) -> bool:
    slot = board.slots.get(slot_id)
    return slot is not None and slot.repair_count > MAX_REPAIRS
