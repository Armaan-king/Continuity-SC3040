"""Graph nodes. Design doc §8.

Nodes orchestrate; they never adjudicate. Every pass/fail here comes from
`engine.rules`, and every decision about what may be changed comes from
`engine.policy`. A node's job is to move parts in and out of slots and narrate it.

## The interrupt gotcha

LangGraph **re-executes an interrupt node when the run resumes** — the node runs again
from the top, with `interrupt()` returning the answer the second time. Anything emitted
before that call is therefore emitted twice.

So `clarify` contains the `interrupt()` and almost nothing else: the `question` frame is
produced by the API layer from the interrupt payload, not written by the node. Keep it
that way when adding interrupts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from typing import Any, Awaitable, Callable

from langgraph.config import get_stream_writer
from langgraph.types import interrupt

from ..api import events
from .. import interpret, reviewer
from ..engine import policy, rules
from ..engine import situation
from ..engine.models import Rail, Requirements, Slot, Verdict
from ..planner import plan as planner
from ..planner import topology
from ..parts import categories
from ..parts.search import Candidate
from . import sourcing
from .state import DesignState


log = logging.getLogger(__name__)


PREFETCHES: dict[str, dict[str, asyncio.Task[sourcing.SearchResults]]] = {}
"""thread_id → slot_id → an in-flight distributor search.

This deliberately sits outside ``DesignState``: LangGraph checkpoints the latter and
an ``asyncio.Task`` cannot survive serialization or a process restart. A resumed run
therefore treats this as a best-effort cache and searches live when it has no entry.
"""

PrecedentLookup = Callable[[str], Awaitable[list[dict[str, Any]]]]
"""A per-run lookup for successful repairs with an exact situation signature."""


def _thread_id(config) -> str | None:
    thread_id = config.get("configurable", {}).get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def clear_prefetches(thread_id: str, *, cancel: bool = False) -> None:
    """Forget one run's searches, cancelling unfinished work when it was abandoned."""
    tasks = PREFETCHES.pop(thread_id, None)
    if cancel and tasks is not None:
        for task in tasks.values():
            if not task.done():
                task.cancel()
            elif not task.cancelled():
                task.exception()


def _emit(payload):
    get_stream_writer()(payload)


def _events(config) -> events.EventStream:
    return config["configurable"]["events"]


async def _prefetch(
    ev: events.EventStream,
    slot_id: str,
    query: str,
    constraint: dict | None,
    purpose: str | None = None,
    board: str | None = None,
) -> sourcing.SearchResults:
    """Search ahead of placement without presenting it as active slot work."""
    _emit(ev.reasoning(slot_id, f"In the background, searching JLCPCB for “{query}”."))
    return await sourcing.find(query, constraint=constraint, purpose=purpose, board=board)


def _precedent_lookup(config) -> PrecedentLookup | None:
    lookup = config.get("configurable", {}).get("precedent_lookup")
    return lookup if callable(lookup) else None


# ── parse_requirements ────────────────────────────────────────────────────────


async def parse_requirements(state: DesignState, config) -> DesignState:
    """Read the brief and lay out the board.

    Falls back to a plain single-rail board when no model is configured — the same
    degraded-but-honest mode the normaliser has. The fallback exists so the system
    still runs without a key, not so it can pretend to have planned.
    """
    ev = _events(config)
    prompt = state.get("prompt", "")
    _emit(ev.reasoning(None, "Reading the brief."))

    board_plan = await planner.plan_board(prompt) or planner.fallback_plan(prompt)
    labels = _listing([board_plan.slots[s].label for s in board_plan.order])
    _emit(ev.reasoning(None, f"{len(board_plan.slots)} parts to source: {labels}."))

    thread_id = _thread_id(config)
    if thread_id is not None and thread_id not in PREFETCHES:
        PREFETCHES[thread_id] = {
            slot_id: asyncio.create_task(
                _prefetch(
                    ev,
                    slot_id,
                    board_plan.queries.get(slot_id, board_plan.slots[slot_id].label),
                    board_plan.slots[slot_id].constraint,
                    board_plan.slots[slot_id].label,
                    prompt,
                )
            )
            for slot_id in board_plan.order
        }

    return {
        "requirements": board_plan.requirements,
        "plan": board_plan,
        "started_at": time.time(),
        "conflicts_resolved": 0,
        "added_slots": 0,
        "verdicts": [],
        "escalation": None,
    }


def _listing(items: list[str]) -> str:
    if len(items) <= 1:
        return items[0] if items else "nothing"
    return f"{', '.join(items[:-1])} and {items[-1]}"


# ── clarify ───────────────────────────────────────────────────────────────────


def needs_clarification(state: DesignState) -> str:
    """Ask only when the board's supply voltage is genuinely unknown.

    A brief that names a voltage has answered the question already, even when no entry
    in the vocabulary matches it — "a 48V industrial bus" is unambiguous and there is no
    48 V industrial supply to classify it as. Asking anyway would send the user to a list
    that cannot express what they just told us.
    """
    requirements = state["requirements"]
    if requirements.input_voltage is not None:
        return "plan"
    return "clarify" if requirements.input_source not in topology.INPUT_SOURCES else "plan"


async def clarify(state: DesignState, config) -> DesignState:
    """Ask what the board is fed from. See the module docstring on re-execution.

    Two things this must not do, both found running §2 on 9 Aug:

    - **Default an unrecognised answer.** It resolved anything it did not match to
      `usb-5v`, silently. Typing "48V industrial bus" produced a 5 V board, and every
      rail, voltage verdict and dissipation figure was then computed against the wrong
      number. There is no default now — an unmatched answer re-asks, which is safe
      because the loop cannot spin: each pass blocks on a new human answer.
    - **Offer a subset.** It listed three of the eleven supplies the engine knows, and
      on all three §2 briefs *none of the three fitted*, so clicking a button was as
      wrong as typing. Every source is offered, so the button path can always be right.
    """
    by_label = {source.label: key for key, source in topology.INPUT_SOURCES.items()}
    attempts = state.get("supply_attempts", 0)

    answer = interrupt(
        {
            "question_id": "supply",
            "text": (
                "I did not recognise that supply, so I have not assumed one. "
                "Please choose from the list below."
                if attempts
                else "What is this board powered from? I could not match it to a supply I know."
            ),
            "suggestions": sorted(by_label),
        }
    )

    # `interpret` can classify prose only into this engine-owned vocabulary. Its exact
    # label and deterministic paths return before a model call; every model failure is
    # None, preserving this node's no-default re-ask below.
    said = str(answer).strip()
    mapped = await interpret.supply_named(said, known=topology.INPUT_SOURCES)
    if mapped is None:
        # Unresolved is the honest state, and it routes straight back here. The counter
        # is what lets the next pass say *why* it is asking again — `input_source` cannot,
        # because it reads UNRESOLVED on the first ask and every re-ask alike.
        return {
            "requirements": replace(state["requirements"], input_source=planner.UNRESOLVED),
            "supply_attempts": attempts + 1,
        }
    return {"requirements": replace(state["requirements"], input_source=mapped)}


# ── plan ──────────────────────────────────────────────────────────────────────


def plan(state: DesignState, config) -> DesignState:
    ev = _events(config)
    board_plan: planner.Plan = state["plan"]
    rails = topology.assemble_rails(
        board_plan.rails, state["requirements"], slot_ids=tuple(board_plan.slots)
    )

    board = topology.Board(state["requirements"], board_plan.slots, rails)
    edges = (
        topology.power_edges(rails)
        + topology.unmodelled(board_plan.slots, rails)
        + topology.planned_data_edges(board_plan.links)
    )
    source = topology.power_source(state["requirements"])
    _emit(ev.plan(board_plan.slots.values(), edges, topology.supply_node(source)))

    supply = rails[topology.INPUT_RAIL_ID]
    names = ", ".join(f"{r.id} at {r.voltage} V" for r in board_plan.rails)
    _emit(ev.reasoning(None, f"{names}, fed from {supply.voltage} V."))

    return {
        "slots": board_plan.slots,
        "rails": rails,
        "pending": list(board_plan.order),
        "current": None,
    }


def replan(state: DesignState, config) -> DesignState:
    """Rebuild the board input after the user named a different known supply."""
    source_name = state["replan_source"]
    requirements = replace(
        state["requirements"], input_source=source_name, input_voltage=None
    )
    board_plan: planner.Plan = state["plan"]
    rails = topology.assemble_rails(
        board_plan.rails, requirements, slot_ids=tuple(board_plan.slots)
    )
    supply = rails[topology.INPUT_RAIL_ID]
    ev = _events(config)

    # Re-announce the board so the supply node stops claiming the old input. A `plan`
    # event is additive on the client — slots and edges it already holds keep their
    # status and their part — so this changes the one thing a replan actually changed.
    source = topology.power_source(requirements)
    _emit(
        ev.plan(
            state["slots"].values(),
            topology.power_edges(rails) + topology.unmodelled(state["slots"], rails),
            topology.supply_node(source),
        )
    )

    names = ", ".join(f"{rail.id} at {rail.voltage} V" for rail in board_plan.rails)
    _emit(ev.reasoning(None, f"{names}, fed from {supply.voltage} V."))

    # A waiver was made against the old supply. Reusing it could hide a fault on a board
    # the user has not seen, so the new board starts with no accepted findings.
    return {
        "requirements": requirements,
        "slots": state["slots"],
        "rails": rails,
        "current": None,
        "constraint": None,
        "guidance": None,
        "escalation": None,
        "verdicts": [],
        "accepted": [],
        "replan_source": None,
        "revalidate_all": True,
    }


# ── select ────────────────────────────────────────────────────────────────────


def _within(slot_id: str, slots: dict) -> str:
    """" among sensor parts", when the slot said what kind of part it wants."""
    category = (slots[slot_id].constraint or {}).get("category")
    return f" among {category} parts" if category else ""


def _rescue_search_message(query: str, subcategory: str) -> str:
    return f"Widened the JLCPCB search for “{query}” to subcategory “{subcategory}”."


def _rescue_search_start_message(query: str, subcategory: str) -> str:
    return (
        f"Fewer than {sourcing.MIN_CANDIDATES} viable candidates for “{query}”; "
        f"widening to JLCPCB subcategory “{subcategory}”."
    )


def _wrong_kind_message(label: str, demoted: tuple[str, ...], total: int) -> str:
    """What a fitness judgement did, in the reader's terms.

    When *every* candidate looks wrong there is nothing better to move it in front of, so
    the honest line says so rather than reporting a reordering that did not happen — that
    is the case worth seeing, because it means the search itself was aimed wrong.
    """
    if len(demoted) >= total:
        return (
            f"None of the {total} candidates looks like a {label.lower()}; "
            f"the shortlist is kept as found."
        )
    noun = "candidate" if len(demoted) == 1 else "candidates"
    return (
        f"Moved {len(demoted)} {noun} down the shortlist for not looking like a "
        f"{label.lower()}: {', '.join(demoted)}."
    )


def _thin_shortlist_message(count: int) -> str:
    noun = "candidate" if count == 1 else "candidates"
    return f"Only {count} viable {noun}; a later repair may have no replacements available."


def _candidate_count_message(count: int) -> str:
    """Not "JLCPCB returned N" — the distributor returned up to forty and this count is
    what survived our own category and range filtering, plus any rescue search."""
    noun = "candidate" if count == 1 else "candidates"
    return f"{count} viable {noun}."


def _normalisation_messages(candidate: Candidate) -> tuple[str, ...]:
    return (f"Reading specs, lifecycle, and datasheet for {candidate.mpn}.",)


def _narrate_normalisation(ev: events.EventStream, slot_id: str, candidate: Candidate) -> None:
    for message in _normalisation_messages(candidate):
        _emit(ev.reasoning(slot_id, message))


def _repair_search_message(label: str, constraint: dict) -> str:
    if not constraint:
        return f"Re-searching for a {label.lower()} that fits."
    applied = ", ".join(f"{key}={value}" for key, value in constraint.items())
    return f"Re-searching with {applied}."


async def _find_with_narration(
    ev: events.EventStream,
    slot_id: str,
    query: str,
    constraint: dict | None,
    purpose: str | None = None,
    board: str | None = None,
) -> sourcing.SearchResults:
    with sourcing.narrate_rescue(
        lambda subcategory: _emit(
            ev.reasoning(slot_id, _rescue_search_start_message(query, subcategory))
        )
    ):
        found = await sourcing.find(
            query, constraint=constraint, purpose=purpose, board=board
        )
    # Never silent. A candidate moved down the list for being the wrong *kind* of part is
    # a judgement the reader is entitled to see, and to disagree with.
    if demoted := getattr(found, "demoted", ()):
        _emit(
            ev.reasoning(
                slot_id, _wrong_kind_message(purpose or slot_id, demoted, len(found))
            )
        )
    return found


async def select(state: DesignState, config) -> DesignState:
    """Search a distributor for the next slot, and type the best hit."""
    ev = _events(config)
    pending = list(state["pending"])
    slot_id = pending.pop(0)
    slots = dict(state["slots"])
    label = slots[slot_id].label

    query = state["plan"].queries.get(slot_id, label)
    # The query, not the label. A slot labelled "Environmental Sensor" was searched as
    # "environmental sensor" and returned fuse clips; the label gave no way to see that.
    _emit(ev.reasoning(slot_id, f"Searching JLCPCB for “{query}”."))
    thread_id = _thread_id(config)
    tasks = PREFETCHES.get(thread_id, {}) if thread_id is not None else {}
    prefetched = tasks.pop(slot_id, None)
    if thread_id is not None and not tasks:
        PREFETCHES.pop(thread_id, None)

    if prefetched is None:
        found = await _find_with_narration(
            ev, slot_id, query, slots[slot_id].constraint, label, state.get("prompt")
        )
    else:
        try:
            found = await prefetched
        except asyncio.CancelledError:
            # Preserve graph cancellation; only an independently cancelled prefetch is
            # an optimisation failure that should be retried live.
            if asyncio.current_task() is not None and asyncio.current_task().cancelling():
                raise
            log.warning("Prefetch cancelled for %s; searching live", slot_id)
            found = await _find_with_narration(
            ev, slot_id, query, slots[slot_id].constraint, label, state.get("prompt")
        )
        except Exception:
            log.warning("Prefetch failed for %s; searching live", slot_id, exc_info=True)
            found = await _find_with_narration(
            ev, slot_id, query, slots[slot_id].constraint, label, state.get("prompt")
        )
    # Rescue first, then the count. `find` already appended the rescue results, so the
    # count is the final one — announcing it before the widening reads as a contradiction.
    if subcategory := getattr(found, "rescue_subcategory", None):
        _emit(ev.reasoning(slot_id, _rescue_search_message(query, subcategory)))
    _emit(ev.reasoning(slot_id, _candidate_count_message(len(found))))

    if not found:
        # A slot with no candidates used to be popped off `pending` and forgotten: the
        # graph showed an empty node, the BOM was a row short, and the run reported
        # `0 conflict · 0 pending · DONE`. An empty search is a finding about the board,
        # not an absence of one, so it gets a verdict like any other.
        #
        # The category is named because it is now the likeliest reason for an empty
        # result, and the two causes need different fixes: a query that finds nothing
        # is the planner's wording, while a query that finds only the wrong kind of part
        # is the planner's category. Neither is diagnosable from "no part found".
        _emit(ev.reasoning(slot_id, f"No {label.lower()} found for “{query}”{_within(slot_id, slots)}."))
        unfilled = Verdict(
            rule="availability",
            status="warn",
            detail=(
                f"No part found for {label} — searched JLCPCB for “{query}”"
                f"{_within(slot_id, slots)}."
            ),
            subject=slot_id,
            involved=(slot_id,),
        )
        _emit(ev.check(unfilled))
        return {
            "pending": pending,
            "current": None,
            "source_next": False,
            "unfilled": [*(state.get("unfilled") or []), slot_id],
        }

    if len(found) < sourcing.MIN_CANDIDATES:
        _emit(ev.reasoning(slot_id, _thin_shortlist_message(len(found))))
    _narrate_normalisation(ev, slot_id, found[0])
    part = await sourcing.choose(found[0])
    _emit(ev.candidate(slot_id, part))

    slots[slot_id] = replace(slots[slot_id], part=part, status="pending")
    return {
        "slots": slots,
        "pending": pending,
        "current": slot_id,
        "source_next": False,
        "candidates": {**state.get("candidates", {}), slot_id: found},
        "cursor": {**state.get("cursor", {}), slot_id: 0},
    }


# ── validate ──────────────────────────────────────────────────────────────────


def validate(state: DesignState, config) -> DesignState:
    """Run the engine over the whole board and report every check it produced."""
    ev = _events(config)
    board = topology.Board(state["requirements"], state["slots"], state["rails"])
    verdicts = _apply_waivers(rules.evaluate(board), state.get("accepted") or [])

    current = state.get("current")
    if state.get("revalidate_all"):
        for verdict in verdicts:
            _emit(ev.check(verdict))
    elif current:
        for verdict in rules.for_subject(verdicts, current):
            _emit(ev.check(verdict))
        # Any *non-pass* verdict attributed to another slot still has to be sent. A
        # rail-level rule names the regulator as its subject while the slot being validated
        # is whatever load sits on the rail, so neither its failures nor its warnings were
        # reaching the screen.
        #
        # Failures were the visible half: a conflict arrived beside a check log showing a
        # clean sweep of five passes. Warnings were the dangerous half. On a coin-cell
        # beacon the last thing the trace ever said about current was
        # `0 mA of 20 mA (0%)` — emitted while the regulator was the only part on the
        # board — and the verdict that replaced it once the MCU landed ("ESP-07S states no
        # draw, so the real figure is higher") named the regulator as its subject and was
        # dropped. The board reported zero conflicts on the one number the brief was about.
        #
        # A pass for another slot is noise; anything else is information the reader needs.
        # The client keys checks by (rule, subject, scope), so re-sending one on a later
        # pass replaces it rather than accumulating.
        # …but only when it is *new or changed*. `validate` runs after every placement, so
        # re-sending an unchanged warning on each pass wrote the same line thirty times into
        # a run's history. `state["verdicts"]` still holds the previous pass here, and a
        # check is keyed by (rule, subject, scope) — so comparing on that key reports a
        # verdict when it appears and when it changes, and stays quiet while it stands.
        previous = {
            (v.rule, v.subject, v.scope): v for v in (state.get("verdicts") or [])
        }
        for verdict in verdicts:
            if verdict.subject == current or verdict.status == "pass":
                continue
            before = previous.get((verdict.rule, verdict.subject, verdict.scope))
            if before is not None and (before.status, before.detail) == (
                verdict.status,
                verdict.detail,
            ):
                continue
            _emit(ev.check(verdict))

    failures = rules.failures(verdicts)
    slots = dict(state["slots"])

    if not failures:
        # A clean board settles *every* placed slot, not just the one last touched. A
        # slot that passed its own checks earlier, then sat through someone else's
        # conflict, would otherwise never be told it was fine — and would stay pending
        # on screen for the rest of the run.
        resolved = topology.resolved_edges(board, verdicts)
        for slot_id, slot in slots.items():
            if slot.part is None or slot.status == "pass":
                continue
            slots[slot_id] = replace(slot, status="pass")
            _emit(
                ev.selection(
                    slot_id,
                    slot.part,
                    "pass",
                    [e for e in resolved if e.target == slot_id],
                )
            )

    return {"verdicts": verdicts, "slots": slots, "revalidate_all": False}


def _apply_waivers(verdicts: list, accepted: list) -> list:
    """Downgrade failures the user explicitly accepted. Never delete them.

    A waiver is not a pass. The check still appears, still carries its evidence, and
    still says what is wrong — it simply no longer stops the run.
    """
    if not accepted:
        return verdicts
    waived = {tuple(entry) for entry in accepted}
    return [
        replace(
            v,
            status="warn",
            detail=f"{v.detail} Accepted by you, so this is not blocking.",
        )
        if v.status == "fail" and (v.rule, v.subject) in waived
        else v
        for v in verdicts
    ]


def after_validate(state: DesignState) -> str:
    if rules.failures(state["verdicts"]):
        return "review"
    return "select" if state["pending"] else "finalize"


# ── review ────────────────────────────────────────────────────────────────────


async def review(state: DesignState, config) -> DesignState:
    """Fence the conflict, then choose inside it.

    The choice is a stub for the reviewer LLM. What is *not* a stub is everything
    around it: `plan_resolution` computes the legal set, `enforce` validates the
    decision against it, and an illegal answer falls back to minimum disruption.
    """
    ev = _events(config)
    board = topology.Board(state["requirements"], state["slots"], state["rails"])
    verdicts = state["verdicts"]
    conflict = rules.failures(verdicts)[0]

    resolution = policy.plan_resolution(conflict, board, rules.passing(verdicts))
    subject_part = board.slots[conflict.subject].part if conflict.subject in board.slots else None
    conflict_signature = situation.signature(
        conflict,
        board,
        category=categories.canonical(subject_part.category) if subject_part else None,
    )
    _emit(ev.conflict(conflict, edge=conflict.scope, signature=conflict_signature))

    if resolution.escalate:
        return {"escalation": resolution.reason}

    _emit(
        ev.reasoning(
            conflict.subject,
            f"Reviewing repair options for {board.slots[conflict.subject].label}.",
        )
    )
    options = {slot_id: _remaining(state, slot_id) for slot_id in resolution.legal}
    precedents: list[dict[str, Any]] = []
    lookup = _precedent_lookup(config)
    if lookup is not None and conflict_signature is not None:
        try:
            precedents = await lookup(conflict_signature)
            if precedents:
                log.info(
                    "offering %d precedent(s) for %s: %s",
                    len(precedents),
                    conflict_signature,
                    ", ".join(sorted({str(item.get("action")) for item in precedents})),
                )
        except Exception:
            # A history lookup is a hint, so losing it must never interrupt the live repair.
            log.warning("precedent lookup failed for %s", conflict_signature, exc_info=True)
    proposal = await reviewer.propose(
        board, conflict, resolution, options, state.get("guidance"), precedents
    ) or _propose(
        state, resolution
    )
    guarded = policy.enforce(proposal, resolution, board)
    if not guarded.accepted and guarded.note:
        # The fence caught something. Say so rather than quietly substituting.
        _emit(ev.reasoning(repair_slot_of(guarded), f"Reviewer overruled: {guarded.note}."))
    repair = guarded.repair

    if repair.action == "escalate":
        return {"escalation": repair.rationale}

    considered = _remaining(state, repair.slot)
    _emit(
        ev.repair(
            slot=repair.slot,
            action=repair.action,
            rationale=repair.rationale,
            constraint=dict(repair.constraint),
            alternatives=(
                sourcing.alternatives(considered, considered[0], repair.rationale)
                if considered
                else []
            ),
        )
    )
    slots = dict(state["slots"])
    slots[repair.slot] = replace(slots[repair.slot], status="conflict")
    return {
        "slots": slots,
        "current": repair.slot,
        "constraint": dict(repair.constraint) or None,
        "repair_action": repair.action,
    }


def repair_slot_of(guarded: policy.Guarded) -> str:
    return guarded.repair.slot


def _propose(state: DesignState, resolution: policy.Resolution):
    """Deterministic fallback when no model is configured.

    Encodes the one inference that matters — a linear regulator failing thermal fails
    again however large the next one is — so the system still self-corrects without a
    key. It is a floor, not a substitute: the model reaches conclusions this cannot.
    """
    from ..engine.models import Repair

    slot_id = resolution.legal[0]
    current = state["slots"][slot_id].part
    remaining = _remaining(state, slot_id)

    # A linear regulator that fails thermal will fail again however large the next one
    # is — dissipation does not depend on the current rating. This is the one place the
    # stub reviewer reaches a conclusion a "try the next part" policy structurally
    # cannot, and it is what the real reviewer LLM will replace.
    thermal = resolution.conflict.rule == "thermal_dissipation"
    linear = current is not None and not current.is_switching
    if thermal and linear:
        return Repair(
            slot=slot_id,
            action="change_topology",
            rationale=(
                "Any linear regulator burns (Vin−Vout) × I as heat, so a larger one "
                "fails the same way. Switching to a buck converter."
            ),
            constraint={
                "topology": "buck",
                "vout": current.vout if current else None,
                "i_out_min": 1.0,
                "efficiency_min": 0.85,
            },
        )

    if not remaining:
        return None
    return Repair(
        slot=slot_id,
        action="swap",
        rationale=f"Trying {remaining[0].mpn}, the next candidate that fits the slot.",
        constraint={},
    )


def after_apply(state: DesignState) -> str:
    if state.get("escalation"):
        return "escalate"
    return "select" if state.get("source_next") else "validate"


def after_review(state: DesignState) -> str:
    return "escalate" if state.get("escalation") else "apply"


# ── apply ─────────────────────────────────────────────────────────────────────


async def apply(state: DesignState, config) -> DesignState:
    """Put the repair into effect — either the next candidate, or a fresh search.

    A `swap` advances through candidates already in hand, which costs nothing. Anything
    that changes what kind of part this is re-searches with the constraint pushed into
    the query, because the next linear regulator in the list is not an answer to a
    thermal failure.
    """
    ev = _events(config)
    slot_id = state["current"]
    slots = dict(state["slots"])
    label = slots[slot_id].label
    constraint = dict(state.get("constraint") or {})
    candidates = dict(state.get("candidates", {}))
    cursor = dict(state.get("cursor", {}))

    if state.get("repair_action") == "add_part" and constraint.get("category") and state.get("current"):
        category = constraint["category"]
        if state.get("added_slots", 0) >= policy.MAX_ADDED_SLOTS:
            # A repair action may create another reviewable slot, so the per-slot repair
            # cap alone cannot prove termination. Bound additions per run as well.
            return {"escalation": "The board already added the maximum number of components."}
        if category not in categories.CATEGORIES:
            return {"escalation": "The requested component category is not recognised."}

        input_rail = topology.Board(state["requirements"], slots, state["rails"]).input_rail(slot_id)
        if input_rail is None:
            return {"escalation": f"Cannot determine where to power the added {category}."}

        if input_rail.id == topology.INPUT_RAIL_ID and any(
            rail_id != topology.INPUT_RAIL_ID for rail_id in state["rails"]
        ):
            return {"escalation": f"Cannot add a component directly to {input_rail.id}."}

        slot_id_added = _added_slot_id(category, slots)
        label = _added_slot_label(category, slot_id_added)
        tier = _added_slot_tier(category)
        added_slot = Slot(
            id=slot_id_added,
            label=label,
            tier=tier,
            pinned=False,
            constraint={"category": category},
        )
        slots[slot_id_added] = added_slot

        declared = [rail for rail_id, rail in state["rails"].items() if rail_id != topology.INPUT_RAIL_ID]
        if input_rail.id != topology.INPUT_RAIL_ID:
            declared = [
                replace(rail, members=(*rail.members, slot_id_added))
                if rail.id == input_rail.id
                else rail
                for rail in declared
            ]
        rails = topology.assemble_rails(declared, state["requirements"], slot_ids=tuple(slots))
        added_edges = [edge for edge in topology.power_edges(rails) if edge.target == slot_id_added]
        _emit(ev.slot_added(added_slot, added_edges))

        slots[slot_id] = replace(slots[slot_id], repair_count=slots[slot_id].repair_count + 1)
        return {
            "slots": slots,
            "rails": rails,
            "pending": [slot_id_added, *state["pending"]],
            "constraint": None,
            "repair_action": None,
            "added_slots": state.get("added_slots", 0) + 1,
            "conflicts_resolved": state.get("conflicts_resolved", 0) + 1,
            "revalidate_all": True,
            "source_next": True,
        }

    named = _named_candidate(constraint, candidates.get(slot_id, []))
    if named is not None:
        # The reviewer picked from the candidates it was shown. No search, no latency,
        # and no chance of the query returning the same failing part — which is what
        # happened repeatedly, because a distributor query cannot express
        # "must tolerate 5 V".
        index = candidates[slot_id].index(named)
        cursor[slot_id] = index
        _narrate_normalisation(ev, slot_id, named)
        replacement = await sourcing.choose(named)
        _emit(ev.candidate(slot_id, replacement))
        slots[slot_id] = replace(
            slots[slot_id],
            part=replacement,
            status="pending",
            repair_count=slots[slot_id].repair_count + 1,
        )
        return {
            "slots": slots,
            "candidates": candidates,
            "cursor": cursor,
            "constraint": None,
            "repair_action": None,
            "conflicts_resolved": state.get("conflicts_resolved", 0) + 1,
        }

    target_rail = constraint.get("rail")
    if isinstance(target_rail, str) and target_rail in state["rails"]:
        rails = {
            rail_id: replace(rail, members=tuple(member for member in rail.members if member != slot_id))
            if slot_id in rail.members
            else rail
            for rail_id, rail in state["rails"].items()
        }
        target = rails[target_rail]
        rails[target_rail] = replace(target, members=(*target.members, slot_id))
        old_rails = [rail.id for rail in state["rails"].values() if slot_id in rail.members]
        old_names = ", ".join(old_rails) or "no rail"
        # The board input rail has no source slot, so `power_edges` draws nothing from it
        # — a slot moved there legitimately has no edge to patch. `policy` refuses such a
        # target today; this stays correct rather than raising if that ever relaxes.
        edge = next(
            (edge for edge in topology.power_edges(rails) if edge.target == slot_id), None
        )
        _emit(ev.reasoning(slot_id, f"Moved {label} from {old_names} to {target_rail}."))
        _emit(ev.selection(slot_id, slots[slot_id].part, "pending", [edge] if edge else []))
        slots[slot_id] = replace(slots[slot_id], repair_count=slots[slot_id].repair_count + 1)
        return {
            "slots": slots,
            "rails": rails,
            "constraint": None,
            "repair_action": None,
            "conflicts_resolved": state.get("conflicts_resolved", 0) + 1,
        }

    if constraint:
        _emit(ev.reasoning(slot_id, _repair_search_message(label, constraint)))
        # The slot's own constraint says what kind of part this is; the repair says what
        # else it must now satisfy. Searching on the repair alone loses the first.
        full = sourcing.merge_constraints(slots[slot_id].constraint, constraint)
        query = state["plan"].queries.get(slot_id, label)
        found = await _find_with_narration(
            ev, slot_id, query, full, label, state.get("prompt")
        )
        if subcategory := getattr(found, "rescue_subcategory", None):
            _emit(ev.reasoning(slot_id, _rescue_search_message(query, subcategory)))
        _emit(ev.reasoning(slot_id, _candidate_count_message(len(found))))
        if not found:
            return {"escalation": f"Nothing matches the revised {label.lower()} constraint."}
        if len(found) < sourcing.MIN_CANDIDATES:
            _emit(ev.reasoning(slot_id, _thin_shortlist_message(len(found))))
        candidates[slot_id] = found
        cursor[slot_id] = 0
    else:
        cursor[slot_id] = cursor.get(slot_id, 0) + 1
        if cursor[slot_id] >= len(candidates.get(slot_id, [])):
            return {"escalation": f"No further candidates for {label}."}

    chosen = candidates[slot_id][cursor[slot_id]]
    _narrate_normalisation(ev, slot_id, chosen)
    replacement = await sourcing.choose(chosen)
    _emit(ev.candidate(slot_id, replacement))

    slots[slot_id] = replace(
        slots[slot_id],
        part=replacement,
        status="pending",
        repair_count=slots[slot_id].repair_count + 1,
    )
    return {
        "slots": slots,
        "candidates": candidates,
        "cursor": cursor,
        "constraint": None,
        "repair_action": None,
        "conflicts_resolved": state.get("conflicts_resolved", 0) + 1,
    }


def _named_candidate(constraint: dict, candidates: list) -> Candidate | None:
    """The candidate the reviewer named, if it really is one of them.

    A fence, not a lookup: the model may only point at parts it was actually shown.
    An unrecognised MPN falls through to the ordinary re-search path.
    """
    wanted = str((constraint or {}).get("mpn") or "").strip().upper()
    if not wanted:
        return None
    return next((c for c in candidates if c.mpn.upper() == wanted), None)


def _added_slot_id(category: str, slots: dict[str, Slot]) -> str:
    """Mint from the engine-owned category, probing until it cannot collide."""
    if category not in slots:
        return category
    suffix = 2
    while f"{category}_{suffix}" in slots:
        suffix += 1
    return f"{category}_{suffix}"


def _added_slot_label(category: str, slot_id: str) -> str:
    label = category.replace("_", " ").title()
    suffix = slot_id.removeprefix(category).removeprefix("_")
    return f"{label} {suffix}" if suffix else label


def _added_slot_tier(category: str):
    if category == "regulator":
        return "power"
    if category == "mcu":
        return "core"
    if category == "passive":
        return "passives"
    return "peripherals"


def _remaining(state: DesignState, slot_id: str) -> list[Candidate]:
    """Candidates for a slot that have not been tried yet."""
    found = state.get("candidates", {}).get(slot_id, [])
    return list(found[state.get("cursor", {}).get(slot_id, 0) + 1 :])


# ── escalate ──────────────────────────────────────────────────────────────────


STOP_OPTION = "Stop and let me change the brief"
CONTINUE_OPTION = "Continue anyway"
RELAX_REQUIREMENT_OPTION = "Relax the stock requirement"

REQUIREMENT_FIELD_BY_RULE: dict[str, str] = {
    "availability": "min_stock",
}
"""Failing rules whose user-stated requirement can be removed without inventing a value."""


def _read_answer(said: str, options: list[str]) -> str:
    """An escalation answer as one of `stop`, `accept` or `redirect`.

    The options are strings *this system generated* and shipped to the browser, so a
    clicked one comes back verbatim and needs no interpretation at all — matching it
    exactly is the whole of that path. What used to happen instead was keyword
    archaeology on prose we wrote ourselves, with two failure modes:

    - `"no"` was tested as a **substring**, so "the node needs 5V", "not enough current"
      and "nominal" all ended the run.
    - Anything that was not a stop word **waived the conflict**. There was no third
      branch, so "switch back to the Li-Ion 5V cell" was filed as approving a 12 V fault
      and the run finished `0 conflict` over a board whose charger was rated to 8 V.

    So: an exact option match is the option. Anything else is `redirect` — the user is
    telling us something, not signing anything off. Defaulting the other way is what
    turned a waiver into a catch-all for faults nobody agreed to.
    """
    if said == STOP_OPTION:
        return "stop"
    if said == CONTINUE_OPTION:
        return "redirect"
    return "accept" if said in options else "redirect"


async def escalate(state: DesignState, config) -> DesignState:
    """Hand the trade-off back, then act on the answer and carry on.

    Two things this must not do, both learned from a real run that reported
    `DONE · 0 conflict · 0 pending` over a board with four empty slots:

    - **Clear `pending`.** The escalated slot is one part of a board. Emptying the queue
      abandoned every remaining slot and went straight to a one-row bill of materials
      that claimed to be complete.
    - **Only echo the answer.** "Relax the stock requirement" was printed and discarded,
      so the run continued into exactly the state the user had just tried to resolve.

    An accepted fault stays visible: `validate` downgrades it to a warning rather than
    deleting it, because the user waiving a check is not the same as the check passing.
    """
    conflict = next(iter(rules.failures(state.get("verdicts") or [])), None)
    options = _escalation_options(conflict, state["requirements"])
    answer = interrupt(
        {
            "question_id": "escalation",
            "text": state.get("escalation") or "This needs a decision from you.",
            "suggestions": options,
        }
    )

    ev = _events(config)
    said = str(answer).strip()
    requirement_field = (
        REQUIREMENT_FIELD_BY_RULE.get(conflict.rule) if conflict is not None else None
    )
    if (
        said == RELAX_REQUIREMENT_OPTION
        and requirement_field is not None
        and getattr(state["requirements"], requirement_field) is not None
    ):
        before = getattr(state["requirements"], requirement_field)
        requirements = replace(state["requirements"], **{requirement_field: None})
        _emit(
            ev.reasoning(
                None,
                f"Relaxed {requirement_field.replace('_', ' ')} from {before} to no minimum.",
            )
        )
        return {
            "requirements": requirements,
            "accepted": [],
            "verdicts": [],
            "escalation": None,
            "guidance": None,
            "revalidate_all": True,
        }

    intent = _read_answer(said, options)
    if intent == "redirect" and said not in options:
        # Exact options are already decided by `_read_answer`; only unrecognised prose
        # reaches the guarded classifier. Its None result deliberately remains redirect.
        classified = await interpret.escalation_intent(
            said,
            options=options,
            question=state.get("escalation") or "This needs a decision from you.",
        )
        if classified is not None:
            intent = classified

    if intent == "stop":
        _emit(ev.reasoning(None, f"Stopping here: {said}"))
        return {"escalation": None, "stopped": True, "pending": []}

    if intent == "redirect" or conflict is None:
        source_name = topology.source_named(said)
        if source_name is not None and source_name != state["requirements"].input_source:
            return {"escalation": None, "replan_source": source_name}
        # Guidance, not a signature. It goes to the reviewer as a constraint on the next
        # attempt rather than being echoed and dropped.
        _emit(ev.reasoning(None, f"Taking that into account: {said}"))
        return {"escalation": None, "guidance": said, "verdicts": []}

    waived = [*(state.get("accepted") or []), (conflict.rule, conflict.subject)]
    _emit(ev.reasoning(None, acceptance_message(conflict.rule, state["slots"][conflict.subject].label)))
    return {"escalation": None, "accepted": waived, "verdicts": []}


def acceptance_message(rule: str, slot_label: str) -> str:
    """The line the trace shows when a user waives a finding.

    **This wording is parsed.** `api/memory.py` reads it to record a finding as `accepted`
    rather than `unresolved`, because accepting an escalation has no structured signal —
    the narration is the only evidence that it happened. Both sides go through this
    function so a rewording cannot silently stop acceptances being remembered.

    If a structured signal is ever added to the event contract, delete the parsing rather
    than keeping two mechanisms.
    """
    return (
        f"Accepted on your say-so — {rule.replace('_', ' ')} on "
        f"{slot_label} stays on the board as a warning."
    )


def _escalation_options(conflict, requirements: Requirements | None = None) -> list[str]:
    """Answers that name the actual trade-off rather than offering a generic yes/no."""
    if conflict is None:
        return [CONTINUE_OPTION, STOP_OPTION]
    named = {
        "availability": "Accept the stock level",
        "current_budget": "Accept the current budget",
        "thermal_dissipation": "Accept the temperature",
        "voltage_overlap": "Accept the voltage mismatch",
        "pin_budget": "Accept the pin count",
        "interface_role_match": "Accept the interface mismatch",
    }
    options = [named.get(conflict.rule, "Accept this and continue")]
    field = REQUIREMENT_FIELD_BY_RULE.get(conflict.rule)
    if field is not None and requirements is not None and getattr(requirements, field) is not None:
        options.append(RELAX_REQUIREMENT_OPTION)
    return options + [STOP_OPTION]


def after_escalate(state: DesignState) -> str:
    """A stop ends the run; a supply change rebuilds rails before revalidation."""
    if state.get("stopped"):
        return "finalize"
    return "replan" if state.get("replan_source") else "validate"


# ── finalize ──────────────────────────────────────────────────────────────────


def finalize(state: DesignState, config) -> DesignState:
    ev = _events(config)
    rows, total = [], 0.0
    for slot_id, slot in state["slots"].items():
        if slot.part is None:
            continue
        rows.append(events.bom_row(slot_id, slot.part))
        total += slot.part.unit_price or 0.0

    _emit(ev.bom(rows, total))
    _emit(
        ev.done(
            slots=len(state["slots"]),
            placed=len(rows),
            conflicts_resolved=state.get("conflicts_resolved", 0),
            elapsed_s=time.time() - state.get("started_at", time.time()),
        )
    )
    thread_id = _thread_id(config)
    if thread_id is not None:
        clear_prefetches(thread_id)
    return {}
