"""Choosing how to resolve a conflict the engine has already proved.

The third and last place a model touches the board, and the most constrained. The
engine decided what is broken and computed which slots may be changed; this picks one
of them and says what to do. `policy.enforce` then checks the answer against that set
before anything is applied.

## What it cannot do, structurally

- **Dispute the finding.** The conflict is an input, not a question. There is no field
  in the reply for "actually this is fine", so the model has nowhere to put it.
- **Reach outside the fence.** It is shown only the legal slots. Naming anything else
  is rejected by `policy.enforce` and replaced with the minimum-disruption fallback.
- **Change what a pinned slot is for.** `policy.PINNED_ACTIONS` allows a like-for-like
  `swap`, a `change_rail`, and `escalate` on a slot the user named; `change_topology`
  on their display is refused.
- **Invent a constraint.** Only the keys in `CONSTRAINT_FIELDS` survive, and only with
  the right type — the rest is dropped before it can reach a search query.

## Why a model at all

A mechanical policy fetches progressively larger regulators against a thermal failure
and fails every time, because dissipation does not depend on the current rating. The
conclusion that matters — *stop replacing the part, change the topology* — is a
different kind of answer, and it is the one thing here worth a model. The fence is what
makes it safe to ask.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Sequence

from . import llm
from .engine import policy
from .engine.models import Board, Repair, RepairAction, Verdict
from .parts import categories

log = logging.getLogger(__name__)

ALL_ACTIONS: tuple[str, ...] = RepairAction.__args__  # type: ignore[attr-defined]

ACTIONS: tuple[str, ...] = ("swap", "change_rail", "change_topology", "add_part", "escalate")
"""What `graph.apply` can actually carry out.

Offering a model a choice the system cannot honour is the same failure as letting it
name a slot outside the fence, so unimplemented actions are not offered."""

CONSTRAINT_FIELDS: dict[str, type] = {
    "mpn": str,
    "topology": str,
    "vout": float,
    "i_out_min": float,
    "vin_min": float,
    "rated_to": float,
    "rated_from": float,
    "package": str,
    "efficiency_min": float,
    "rail": str,
    "category": str,
}
"""What a repair may demand of the next search. Anything else is dropped.

Deliberately narrow: each key has an implemented consumer in `sourcing` or `graph.apply`;
anything else is dropped here where the reason is visible.
"""

MAX_RATIONALE = 400
"""It goes on screen as prose. Longer than this is an essay, not a reason."""

SYSTEM = f"""You repair printed circuit board designs.

A deterministic engine has already found a definite fault and proved it. You do not
decide whether the fault is real — it is. You decide what to do about it.

You will be given the failing check, the evidence behind it, and the slots you are
allowed to change. Return ONE JSON object:

  slot        the slot id you are changing — MUST be one from the allowed list
  action      one of: {", ".join(ACTIONS)}
  rationale   one or two sentences, plain English, for a hardware engineer to read
  constraint  what the replacement must satisfy (may be empty)

constraint may only contain these keys:
  mpn             PREFER THIS. If one of the replacements listed for the slot fixes the
                  problem, name it exactly. It is already available, so this is instant
                  and certain — no re-search, no guessing.
  topology        "ldo", "buck", "boost" — use when the KIND of part must change
  vout            output voltage in volts
  i_out_min       minimum output current in amps
  vin_min         the input voltage the part must ACCEPT, in volts. Use this when the
                  fault is that the supply is too high for the part — a 48 V rail on a
                  regulator rated to 40 V is `vin_min: 48`. It is applied to the whole
                  candidate list, so it finds parts a search cannot ask for.
  rated_to        the hot end the part must reach, in °C. Use this when the fault is that
                  the part is not rated for the board's operating range — a 70 °C part on
                  a board that must reach 85 °C is `rated_to: 85`. Applied to the whole
                  candidate list, so it finds parts a search cannot ask for.
  rated_from      the cold end the part must reach, in °C, and normally negative. Use it
                  when the fault is the *low* end — a part rated to −25 °C on a board that
                  must survive −40 °C is `rated_from: -40`. Without it an outdoor brief
                  re-searches and gets the same part back, because nothing in the query
                  excludes it.
  package         a package name, when thermal dissipation demands a bigger one
  efficiency_min  0-1 fraction
  rail            an id from `board_rails`, only for `change_rail`; copy the id exactly.
                  Never invent a rail and never provide a voltage instead of an id.

Look at `replacements_available` before anything else. If one of them clears the
finding — a part rated for the rail voltage, or with enough output current — name it in
`constraint.mpn`. A constraint that cannot name a part triggers a fresh search, and a
search cannot express "must tolerate 5 V", so it will very likely return the same part
and the same failure.

Choosing the action — these five and nothing else:
  swap              a different part of the same kind will fix it
  change_rail       the part is right but is attached to the wrong rail. Set
                    `constraint.rail` to one existing id from `board_rails`.
  change_topology   the same kind of part will fail the same way however you size it
  add_part          the board is missing a component, not holding the wrong one. Set
                    `constraint.category` to the exact kind to add from `part_categories`.
                    Do not name a part number, a description, or a voltage.
  escalate          nothing available fixes this, or the fault is in the brief itself
                    rather than in any part — the user must decide

Think about WHY the check failed, not just which part is nearest. A linear regulator
that overheats dissipates (Vin - Vout) x I as heat, so a larger linear regulator
dissipates exactly the same and fails identically — that is a change_topology, not a
swap. A part that is merely too small is a swap.

For a slot with `must_supply_rail`, the part there has to make `must_supply_rail.volts`
out of `input_voltage`. Compare those two numbers before anything else:
  input_voltage > rail volts   a buck or an LDO can do it
  input_voltage < rail volts   ONLY a boost can do it — no buck of any size will work,
                               so that is change_topology with topology "boost"
Both numbers are given to you. Never say the required output voltage is unknown.

Precedents are actions that resolved the same structural situation on an earlier board.
Treat one as a suggestion for what to try first; the board in front of you still decides.
Ignore it when this board's situation differs.

`vout` is null for an ADJUSTABLE regulator, because it has no single output until its
feedback resistors are chosen — read `vout_min`/`vout_max` for what it can be set to. An
adjustable part covering the rail voltage is a valid answer where a fixed one is not.

Slots marked "pinned" were named by the user. You may swap, change their rail, or
escalate those; you may not change what they are for.

Return the JSON object and nothing else."""


# ── the prompt ────────────────────────────────────────────────────────────────


def _supplied_rail(board: Board, slot_id: str) -> dict[str, Any] | None:
    """The rail this slot is the source of — what a regulator here has to make."""
    rail = next((r for r in board.rails.values() if r.source == slot_id), None)
    return None if rail is None else {"id": rail.id, "volts": rail.voltage}


def _input_voltage(board: Board, slot_id: str) -> float | None:
    rail = board.input_rail(slot_id)
    return None if rail is None else rail.voltage


def _describe(board: Board, conflict: Verdict, resolution: policy.Resolution,
              options: Mapping[str, Sequence[Any]], guidance: str | None = None,
              precedents: Sequence[Mapping[str, Any]] = ()) -> str:
    """Everything the reviewer is allowed to know, and nothing it is not.

    `guidance` is what the user typed at an escalation. It informs the *choice* and
    changes nothing about the fence: the legal set is still `resolution.legal`, the
    action must still be one of `ACTIONS`, and `policy.enforce` still checks the answer.
    A sentence from the user is a hint about what to try, never permission to leave the
    fence — which is exactly the standing every other model input here has.
    """
    slots = []
    for slot_id in resolution.legal:
        slot = board.slots[slot_id]
        part = slot.part
        slots.append(
            {
                "id": slot_id,
                "label": slot.label,
                "pinned": slot.pinned,
                "allowed_actions": sorted(policy.allowed_actions(board, slot_id)),
                # What this slot has to *do*, not only what is in it. Without the rail it
                # sources, a reviewer looking at a failing regulator cannot tell whether
                # a boost would help: it knows the input is 3 V and has no idea the
                # output must be 3.3 V. One live run escalated on exactly that, saying
                # "the required output voltage is unknown" about a figure the rail holds.
                "must_supply_rail": _supplied_rail(board, slot_id),
                "input_voltage": _input_voltage(board, slot_id),
                "current_part": None
                if part is None
                else {
                    "mpn": part.mpn,
                    "category": part.category,
                    "package": part.package,
                    "topology": part.topology,
                    "vout": part.vout,
                    "vout_min": part.vout_min,
                    "vout_max": part.vout_max,
                    "i_max": part.i_max,
                },
                "replacements_available": [
                    {"mpn": c.mpn, "package": getattr(c, "package", None)}
                    for c in list(options.get(slot_id, []))[:4]
                ],
            }
        )

    payload: dict[str, Any] = {
        "failing_check": conflict.rule,
        "finding": conflict.detail,
        "evidence": [{"field": e.field, "value": e.value} for e in conflict.evidence[:6]],
        "slots_you_may_change": slots,
        "board_rails": [
            {"id": rail.id} for rail in board.rails.values() if rail.source is not None
        ],
        "part_categories": sorted(categories.CATEGORIES),
    }
    if guidance:
        payload["user_guidance"] = guidance
    if precedents:
        safe_precedents = [
            {"situation": item["signature"], "action": item["action"]}
            for item in precedents
            if isinstance(item.get("signature"), str) and isinstance(item.get("action"), str)
        ]
        if safe_precedents:
            payload["precedents"] = safe_precedents

    return json.dumps(payload, ensure_ascii=False, indent=1)


# ── validation ────────────────────────────────────────────────────────────────


def _clean_constraint(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, expected in CONSTRAINT_FIELDS.items():
        value = raw.get(key)
        if value is None or isinstance(value, bool):
            continue
        if expected is float and isinstance(value, (int, float)):
            out[key] = float(value)
        elif expected is str and isinstance(value, str) and value.strip():
            cleaned = value.strip() if key == "rail" else value.strip().lower()
            if key != "category" or cleaned in categories.CATEGORIES:
                out[key] = cleaned
    return out


def build_repair(raw: Mapping[str, Any]) -> Repair | None:
    """A model reply as a `Repair`, or None if it is not one.

    Returning None is safe: `policy.enforce` treats it exactly like a timeout and
    substitutes the minimum-disruption swap.
    """
    slot = str(raw.get("slot", "")).strip().lower()
    action = str(raw.get("action", "")).strip().lower()
    if not slot or action not in ACTIONS:
        return None

    rationale = str(raw.get("rationale") or "").strip()[:MAX_RATIONALE]
    return Repair(
        slot=slot,
        action=action,  # type: ignore[arg-type]
        rationale=rationale or f"Applying {action.replace('_', ' ')} to {slot}.",
        constraint=_clean_constraint(raw.get("constraint")),
    )


# ── the call ──────────────────────────────────────────────────────────────────


async def propose(
    board: Board,
    conflict: Verdict,
    resolution: policy.Resolution,
    options: Mapping[str, Sequence[Any]],
    guidance: str | None = None,
    precedents: Sequence[Mapping[str, Any]] = (),
) -> Repair | None:
    """Ask for a repair. None when there is no model, or nothing usable came back."""
    if not llm.available() or not resolution.legal:
        return None
    try:
        # The one call worth thinking about: "replace this part" versus "this whole
        # kind of part is wrong" is a judgement, not an extraction.
        reply = await llm.complete_json(
            SYSTEM,
            _describe(board, conflict, resolution, options, guidance, precedents),
            effort=llm.LOW,
        )
    except (llm.LLMUnavailable, ValueError, json.JSONDecodeError) as error:
        log.warning("review failed for %s: %s", conflict.rule, error)
        return None

    repair = build_repair(reply)
    if repair is None:
        log.warning("reviewer returned nothing usable for %s", conflict.rule)
    return repair
