"""Build reproducible documents; no database, model, or network dependencies."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields, replace
from typing import Any

from ..engine import rules
from ..engine.models import Board, PartSpec, Rail, Requirements, Slot, Verdict, Port, SignalNet, RULE_NAMES
from ..planner import topology
from ..planner.requirements import explicit_requirements

SCHEMA_VERSION = 1
RULESET_VERSION = "2026-10-review-3"


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=list).encode()).hexdigest()


def board_from_document(document: dict) -> Board:
    raw = document["board"]
    requirement_keys = {f.name for f in fields(Requirements)}
    requirements = Requirements(**{k: v for k, v in raw["requirements"].items() if k in requirement_keys})
    part_keys = {f.name for f in fields(PartSpec)}
    slots = {}
    for key, value in raw["slots"].items():
        item = dict(value)
        item.setdefault("quantity", document.get("quantities", {}).get(key, 1))
        part = item.get("part")
        if part:
            part = {k: v for k, v in part.items() if k in part_keys}
            part["interfaces"] = tuple(part.get("interfaces", ()))
            part["research"] = tuple(part.get("research", ()))
            item["part"] = PartSpec(**part)
        slots[key] = Slot(**item)
    rails = {key: Rail(**{**value, "members": tuple(value["members"])}) for key, value in raw["rails"].items()}
    # Older BOM documents encoded quantities by repeating rail members. Migrate on
    # read so restoring a revision cannot multiply the same quantity twice.
    if document.get("origin") == "bom" and any("quantity" not in s for s in raw["slots"].values()):
        rails = {key: replace(rail, members=tuple(dict.fromkeys(rail.members))) for key, rail in rails.items()}
    nets = tuple(SignalNet(**{**net, "ports": tuple(Port(**port) for port in net["ports"])}) for net in raw.get("signal_nets", []))
    return Board(requirements, slots, rails, nets)


def evaluate(board: Board) -> list[Verdict]:
    """One evaluation boundary for imports, generation, and revision edits."""
    result = rules.evaluate(board)
    for slot in board.slots.values():
        if slot.part is None:
            result.append(Verdict("availability", "warn", f"No exact component has been resolved for {slot.label}.", slot.id, unchecked=True))
    if not board.rails:
        subject = next(iter(board.slots), "board")
        for rule in ("voltage_overlap", "current_budget", "thermal_dissipation"):
            result.append(Verdict(rule, "warn", "Connections are unknown; this check cannot be evaluated.", subject, unchecked=True))
    return result


def document(board: Board, *, prompt: str = "", verdicts: list[Verdict] | None = None,
             quantities: dict[str, int] | None = None, origin: str = "brief",
             confirmed: list[str] | None = None, decisions: list[dict] | None = None) -> dict:
    from ..api import events

    if quantities:
        board = replace(board, slots={key: replace(slot, quantity=quantities.get(key, slot.quantity))
                                      for key, slot in board.slots.items()})
    verdicts = evaluate(board) if verdicts is None else verdicts
    quantities = quantities or {key: slot.quantity for key, slot in board.slots.items()}
    confirmed = confirmed or []
    rows = [events.bom_row(key, slot.part, quantities.get(key, 1))
            for key, slot in board.slots.items() if slot.part]
    findings = []
    for verdict in verdicts:
        status = verdict.original_status or verdict.status
        findings.append({
            "id": digest([verdict.rule, verdict.subject, verdict.scope])[:20],
            "rule": verdict.rule, "subject": verdict.subject, "scope": verdict.scope,
            "outcome": "UNKNOWN" if verdict.unchecked else "FAIL" if status == "fail" else "PASS",
            "severity": "error" if status == "fail" else "warning" if verdict.status == "warn" else "info",
            "accepted": verdict.accepted, "detail": verdict.detail,
            "involved": list(verdict.involved), "evidence": [asdict(e) for e in verdict.evidence],
        })
    # A rule that produced no result is never counted as a passing check.
    seen = {finding["rule"] for finding in findings}
    prechecked = {"data_integrity"} if any(s.part for s in board.slots.values()) else set()
    if board.rails:
        prechecked.add("power_topology")
    coverage = [{"rule": name, "outcome": "EVALUATED" if name in seen | prechecked else "NOT_APPLICABLE",
                 "note": "See individual findings." if name in seen else "Input checks found no inconsistencies." if name in prechecked else "No applicable check was emitted for this model."}
                for name in RULE_NAMES]
    explicit = explicit_requirements(prompt)
    defaults = asdict(Requirements())
    requirements = [{"field": name, "value": value,
                     "basis": "confirmed" if name in confirmed else "stated" if name in explicit and (explicit[name] == value or isinstance(value, (list, tuple)) and list(explicit[name]) == list(value))
                     else "assumed" if value == defaults[name] else "inferred"}
                    for name, value in asdict(board.requirements).items()]
    evidence = [{"slot": key, "mpn": slot.part.mpn, "manufacturer": slot.part.manufacturer,
                 "package": slot.part.package, "facts": dict(slot.part.fact_evidence),
                 "documents": list(slot.part.research)}
                for key, slot in board.slots.items() if slot.part]
    edges = topology.resolved_edges(board, verdicts)
    supply = topology.supply_node(topology.power_source(board.requirements)) if origin != "bom" else None
    view = events.EventStream("snapshot").board(board.slots.values(), edges, verdicts, supply)
    for name in ("type", "thread_id", "seq"):
        view.pop(name, None)
    counts = {key.lower(): sum(f["outcome"] == key for f in findings) for key in ("PASS", "FAIL", "UNKNOWN")}
    counts["accepted"] = sum(f["accepted"] for f in findings)
    limitations = ["Signal pin connectivity, timing, and PCB layout are not verified by this model.",
                   "Thermal and battery estimates depend on stated operating conditions and assumptions."]
    if any(row.get("unit_price") is None for row in rows):
        limitations.append("The BOM subtotal excludes components whose price is unavailable.")
    if origin == "bom" and any(not rail.basis or "unconfirmed" in rail.basis for rail in board.rails.values()):
        limitations.append("Power connections inferred from a BOM require confirmation against the circuit.")
    return {"schema_version": SCHEMA_VERSION, "ruleset_version": RULESET_VERSION,
            "origin": origin, "prompt": prompt, "board": asdict(board), "view": view,
            "quantities": quantities, "requirements": requirements, "evidence": evidence,
            "bom": {"rows": rows, "total": round(sum((r.get("unit_price") or 0) * r.get("qty", 1) for r in rows), 2), "currency": "USD"},
            "report": {"findings": findings, "coverage": coverage, "counts": counts, "limitations": limitations},
            "decisions": decisions or []}


def compare(before: dict, after: dict) -> dict:
    old, new = before["board"]["slots"], after["board"]["slots"]
    changes = []
    for key in sorted(old.keys() | new.keys()):
        a, b = old.get(key, {}).get("part"), new.get(key, {}).get("part")
        identity = lambda p: (p.get("manufacturer"), p.get("mpn"), p.get("package")) if p else None
        if identity(a) != identity(b) or before.get("quantities", {}).get(key, 1) != after.get("quantities", {}).get(key, 1):
            changes.append({"slot": key, "before": identity(a), "after": identity(b),
                            "quantity_before": before.get("quantities", {}).get(key, 1),
                            "quantity_after": after.get("quantities", {}).get(key, 1)})
    return {"components": changes,
            "connections_changed": any(before["board"].get(key) != after["board"].get(key) for key in ("signal_nets", "rails")),
            "requirements": [{"field": key, "before": value, "after": after["board"]["requirements"].get(key)}
                             for key, value in before["board"]["requirements"].items()
                             if value != after["board"]["requirements"].get(key)],
            "cost_delta": round(after["bom"]["total"] - before["bom"]["total"], 2),
            "before": before["report"]["counts"], "after": after["report"]["counts"]}
