"""Validate measurements and power connections before numerical evaluation."""
from __future__ import annotations

import math
from dataclasses import replace

from .models import Board, Evidence, Verdict

NONNEGATIVE = ("vmin", "vmax", "vout_min", "vout_max", "i_typ", "i_peak", "i_max",
               "pins_required", "pins_available", "stock", "lead_time_days", "unit_price")
POSITIVE = ("theta_ja", "efficiency")
SIGNED = ("temp_min", "temp_max")
PRECHECK_NAMES = ("data_integrity", "power_topology")


def number(value, minimum=None, *, positive=False) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and (minimum is None or value >= minimum)
            and (not positive or value > 0))


def prepare(board: Board) -> tuple[Board, list[Verdict]]:
    """Return an evaluation copy. Invalid source data is retained in the saved document."""
    findings = []
    req = board.requirements
    invalid_requirements = []
    for name in ("current_margin", "min_stock", "max_lead_days", "max_package_mm", "input_voltage",
                 "lifetime_hours", "supply_capacity_mah"):
        value = getattr(req, name)
        if (value is None and name in {"current_margin", "max_lead_days"}) or (value is not None and not number(value, 0)):
            invalid_requirements.append(name)
    if not number(req.ambient_c, -273.15):
        invalid_requirements.append("ambient_c")
    if (not isinstance(req.temp_range, (list, tuple)) or len(req.temp_range) != 2
            or not all(number(v, -273.15) for v in req.temp_range) or req.temp_range[0] > req.temp_range[1]):
        invalid_requirements.append("temp_range")
    if invalid_requirements:
        findings.append(Verdict("data_integrity", "warn",
            "Invalid operating requirements prevent evaluation: " + ", ".join(invalid_requirements) + ".",
            next(iter(board.slots), "board"), scope="requirements", unchecked=True))
    slots = dict(board.slots)
    for key, slot in slots.items():
        if not number(slot.quantity, positive=True) or slot.quantity != int(slot.quantity):
            findings.append(Verdict("power_topology", "warn", f"{key} has an invalid component quantity.", key, unchecked=True))
            slot = slots[key] = replace(slot, quantity=1)
        part = slot.part
        if part is None:
            continue
        disputed = [claim for doc in part.research for claim in doc.get("claims", [])
                    if claim.get("status") == "conflict"]
        if disputed:
            findings.append(Verdict("data_integrity", "warn",
                f"{part.mpn}: source specifications disagree; disputed measurements are excluded until reviewed.",
                key, scope="research", unchecked=True,
                evidence=tuple(Evidence(key, claim.get("parameter", "research"), claim.get("quote", ""), doc.get("url"))
                               for doc in part.research for claim in doc.get("claims", []) if claim.get("status") == "conflict")))
        invalid = set()
        for name in (*NONNEGATIVE, *POSITIVE, *SIGNED):
            value = getattr(part, name)
            if value is not None and not number(value, 0 if name in NONNEGATIVE else -273.15 if name in SIGNED else None,
                                                positive=name in POSITIVE):
                invalid.add(name)
            if name in {"pins_available", "pins_required", "stock", "lead_time_days"} and number(value) and value != int(value):
                invalid.add(name)
        if number(part.efficiency) and part.efficiency > 1:
            invalid.add("efficiency")
        for lo, hi in (("vmin", "vmax"), ("vout_min", "vout_max"), ("temp_min", "temp_max"),
                       ("i_typ", "i_peak")):
            a, b = getattr(part, lo), getattr(part, hi)
            if number(a) and number(b) and a > b:
                invalid.update((lo, hi))
        if invalid:
            names = sorted(invalid)
            findings.append(Verdict("data_integrity", "warn",
                f"{part.mpn}: invalid or contradictory measurements ({', '.join(names)}) were excluded from checks.",
                key, unchecked=True, evidence=tuple(Evidence(key, name, str(getattr(part, name))) for name in names)))
            slots[key] = replace(slot, part=replace(part, **dict.fromkeys(invalid)))

    rails = {}
    for key, rail in board.rails.items():
        problems = []
        if not number(rail.voltage, positive=True):
            findings.append(Verdict("power_topology", "warn", f"{key} has no valid positive supply voltage.",
                rail.source or next(iter(slots), "board"), scope=key, unchecked=True))
            continue
        if rail.source and rail.source not in slots:
            problems.append("its source component does not exist")
        if rail.source in rail.members:
            problems.append("its source is connected to its own output")
        if len(rail.members) != len(set(rail.members)):
            problems.append("a load is listed more than once")
        if any(member not in slots for member in rail.members):
            problems.append("a connected component does not exist")
        limit = rail.i_limit
        if limit is not None and not number(limit, 0):
            problems.append("its current limit is invalid")
            limit = None
        rails[key] = replace(rail, i_limit=limit, members=tuple(dict.fromkeys(m for m in rail.members if m in slots)))
        if problems:
            findings.append(Verdict("power_topology", "warn", f"{key}: {'; '.join(problems)}.",
                rail.source if rail.source in slots else next(iter(rails[key].members), next(iter(slots), "board")),
                scope=key, unchecked=True))

    for key in slots:
        inputs = [r.id for r in rails.values() if key in r.members]
        outputs = [r.id for r in rails.values() if r.source == key]
        detail = None
        if len(inputs) > 1:
            detail = f"{key} is connected to multiple supplies ({', '.join(inputs)}); separate power pins are not modelled."
        elif outputs and not inputs:
            detail = f"{key} produces a rail but has no declared input supply."
        elif len(outputs) > 1:
            detail = f"{key} produces multiple rails; independent output ratings are not available. Total input demand includes every output."
        elif outputs and slots[key].quantity != 1:
            detail = f"{key} represents multiple power sources; current sharing and per-instance connections are unknown."
        if detail:
            findings.append(Verdict("power_topology", "warn", detail, key, unchecked=True))

    # Iterative traversal also handles corrupt or legacy checkpoints without recursion.
    parents = {key: {r.source for r in rails.values() if key in r.members and r.source} for key in slots}
    for key in slots:
        pending, visited = list(parents[key]), set()
        while pending:
            parent = pending.pop()
            if parent == key:
                findings.append(Verdict("power_topology", "warn",
                    f"{key} participates in a power cycle; an independent upstream supply must be established.", key, unchecked=True))
                break
            if parent not in visited:
                visited.add(parent)
                pending.extend(parents.get(parent, ()))
    merged = {}
    for finding in findings:
        identity = (finding.rule, finding.subject, finding.scope)
        previous = merged.get(identity)
        merged[identity] = finding if previous is None else replace(previous,
            detail=previous.detail + " " + finding.detail, evidence=previous.evidence + finding.evidence)
    return replace(board, slots=slots, rails=rails), list(merged.values())
