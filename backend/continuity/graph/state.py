"""The single checkpointed object the graph carries. Design doc §8."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from ..engine.models import Rail, Requirements, Slot, Verdict


def _replace(_old: Any, new: Any) -> Any:
    """Last write wins. Nodes return whole collections, not deltas."""
    return new


class DesignState(TypedDict, total=False):
    prompt: str
    requirements: Requirements

    plan: Any
    """The planner's board layout: slots, rails, search queries, bus links."""

    slots: Annotated[dict[str, Slot], _replace]
    rails: Annotated[dict[str, Rail], _replace]

    pending: Annotated[list[str], _replace]
    """Slots awaiting sourcing, in placement order. `unfilled` can remain when this is empty."""

    current: str | None
    """The slot being placed or repaired."""

    revalidate_all: bool
    """Set by a node that changed something board-wide. `validate` then reports every
    verdict rather than only the slot it just touched, and clears the flag."""

    candidates: Annotated[dict[str, list], _replace]
    """Raw search hits per slot. A bounded shortlist may be normalised for comparison."""

    attempted: Annotated[dict[str, list[str]], _replace]
    """Component identities already placed, retained across repeated repair searches."""

    cursor: Annotated[dict[str, int], _replace]
    """Which candidate is currently in each slot."""

    constraint: dict | None
    """Set by `review`, consumed by `apply`. A constraint means re-search rather than
    advance — the next part in the old list is not an answer to a topology change."""

    repair_action: str | None
    """The reviewed action consumed by `apply`; needed to distinguish `add_part` from a
    category constraint on an ordinary replacement."""

    source_next: bool
    """An added slot must be filled before the graph may revalidate the board."""

    verdicts: Annotated[list[Verdict], _replace]
    conflicts_resolved: int
    added_slots: int
    """How many late slots this run has declared. Bounded by `policy.MAX_ADDED_SLOTS`."""
    started_at: float

    accepted: Annotated[list, _replace]
    """(rule, subject, scope, evidence hash) records the user waived; legacy pairs remain readable. `validate` downgrades these to warnings —
    a waiver is not a pass, so the finding stays on screen with its evidence."""

    stopped: bool
    """The user chose to stop rather than accept. Ends the run at `finalize`."""

    escalation: str | None
    """Set when the fence closed and the user has to decide. Drives a `question`."""

    supply_attempts: int
    """How many times `clarify` has been answered without resolving the supply.

    `clarify` cannot tell "first ask" from "re-ask" by looking at `input_source`: it is
    `UNRESOLVED` in both cases, because an unrecognised answer is exactly why we are back
    here. Without a counter the dialog returns identical and the Send button looks dead."""

    guidance: str | None
    """Legacy reviewer guidance retained for older checkpoints. New revision instructions
    enter `reconfiguration` so they cannot be discarded by the automatic repair limit."""

    replan_source: str | None
    """A user-named replacement input supply, consumed by `replan` before validation."""

    reconfiguration: str | None
    """An explicit structural revision, consumed once by the reconfiguration node."""
    recovery_target: str | None
    replacement_history: Annotated[dict[str, list], _replace]
    """Up to six previous selections per slot, available for explicit rollback and revalidation."""
    researched_alternatives: Annotated[dict[str, list[dict]], _replace]
    """Retrieved web candidates stay selectable without paying for another search."""
    recovery_action: str | None
    """A user-requested candidate search or controller addition; never a waiver."""
    offered_alternatives: Annotated[list[dict], _replace]
    """Server-owned alternatives awaiting an explicit choice; never applied from browser data."""
    alternative_slot: str | None
    manual_choice: bool

    unfilled: Annotated[list[str], _replace]
    """Slots whose search returned nothing.

    Kept separately from `pending` because they are neither waiting nor placed: the run
    can finish with one, but the board is short a part and `finalize` has to say so.
    Without this they left `pending` and vanished from every count."""
