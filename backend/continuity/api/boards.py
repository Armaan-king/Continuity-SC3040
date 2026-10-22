"""Restore recorded boards, including BOM runs that have no graph checkpoint."""

from __future__ import annotations

from typing import Any


def revision_trace(revision: dict[str, Any], thread_id: str) -> list[dict[str, Any]]:
    """Present saved findings after an edit; never replay stale run checks as current.

    These are display events, not appended to the run log. Its original trace and
    immutable revisions remain available for history and audit.
    """
    from .events import EventStream
    from ..engine.models import Evidence, Verdict

    stream = EventStream(thread_id)
    result = [stream.reasoning(None, "Saved revision revalidated. The following checks use the current requirements, components and connections. No new AI sourcing run was started.")]
    for finding in revision["document"]["report"]["findings"]:
        failed = finding["outcome"] == "FAIL"
        accepted = finding.get("accepted", False)
        result.append(stream.check(Verdict(
            rule=finding["rule"], subject=finding["subject"], scope=finding.get("scope"),
            status="fail" if failed and not accepted else "warn" if finding["severity"] == "warning" or accepted else "pass",
            detail=finding["detail"], unchecked=finding["outcome"] == "UNKNOWN",
            accepted=accepted, original_status="fail" if failed and accepted else None,
            evidence=tuple(Evidence(**item) for item in finding["evidence"]),
        )))
    return result


def recorded_board(trace: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(trace):
        if event.get("type") == "board":
            return {key: event.get(key) for key in ("slots", "edges", "supply", "conflicts")}

    # Old recordings have only incremental events. Replay their data without timers
    # or a new parts lookup, so existing saved BOMs benefit from the same fix.
    slots: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    supply = None
    conflicts: dict[tuple, dict] = {}
    checks: dict[tuple, str] = {}
    for event in trace:
        kind = event.get("type")
        if kind in {"plan", "slot_added"}:
            declared = event.get("slots", []) if kind == "plan" else [event["slot"]]
            for slot in declared:
                slots[slot["id"]] = {
                    "status": "pending", "part": None, "constraint": None, "repair_count": 0,
                    **slots.get(slot["id"], {}), **slot,
                }
            for edge in event.get("edges", []):
                previous = edges.get(edge["id"], {})
                edges[edge["id"]] = {**previous, **edge,
                                      "status": previous.get("status", edge["status"])}
            supply = event.get("supply", supply)
        elif kind in {"candidate", "selection"} and event.get("slot") in slots:
            slot = slots[event["slot"]]
            slot["part"] = event["part"]
            slot["status"] = event.get("status", "pending")
        elif kind == "repair" and event.get("slot") in slots:
            slot = slots[event["slot"]]
            slot["constraint"] = event.get("constraint")
            slot["repair_count"] += 1
        elif kind == "check":
            checks[(event["rule"], event["slot"], event.get("scope"))] = event["status"]
        elif kind == "conflict":
            subject = event.get("subject") or next(
                (e["slot"] for e in event.get("evidence", []) if e.get("slot") in slots),
                next(iter(event.get("involved", [])), None),
            )
            conflicts[(event["rule"], subject, event.get("edge"))] = {**event, "subject": subject}
        if kind in {"selection", "conflict"}:
            for patch in event.get("edges", []):
                if patch["id"] in edges:
                    edges[patch["id"]].update(patch)
    if not slots:
        return None
    failing = {subject for (_, subject, _), status in checks.items() if status == "fail"}
    active = [value for key, value in conflicts.items() if checks.get(key, "fail") == "fail"]
    for slot in slots.values():
        if slot["part"] is not None:
            if slot["id"] in failing:
                slot["status"] = "conflict"
            elif slot["status"] == "conflict" and any(subject == slot["id"] for _, subject, _ in checks):
                slot["status"] = "pass"
    active = [conflict for conflict in active if slots.get(conflict["subject"], {}).get("status") == "conflict"]
    return {"slots": list(slots.values()), "edges": list(edges.values()),
            "supply": supply, "conflicts": active}
