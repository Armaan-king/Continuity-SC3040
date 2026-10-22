"""Actionable review guidance derived from a saved design, without API calls.

Questions describe user intent; evidence tasks describe component facts. Neither
changes a verdict or fills a missing operand. This also works for older revisions.
"""
from __future__ import annotations

import re

# Examples of user-selected goals, never defaults or retrieved component facts.
ANSWER_OPTIONS = {
    "lifetime_hours": [("1 day", 24), ("3 days", 72), ("1 week", 168)],
    "temp_range": [("0 to 40 °C", [0, 40]), ("−10 to 60 °C", [-10, 60]), ("−40 to 85 °C", [-40, 85])],
    "current_margin": [("15%", 0.15), ("30%", 0.30), ("50%", 0.50)],
    "max_package_mm": [("10 mm", 10), ("15 mm", 15), ("25 mm", 25)],
}


def battery_brief(prompt: str, source: str = "") -> bool:
    return "battery" in source or "liion" in source or bool(re.search(
        r"\bbatter(?:y|ies)\b|\bli[- ]?ion\b|\blipo\b|\bcoin cell\b", prompt, re.I))


def review_guidance(document: dict) -> dict:
    from ..planner.topology import INPUT_SOURCES
    board = document["board"]
    values = board["requirements"]
    bases = {row["field"]: row["basis"] for row in document.get("requirements", [])}
    prompt = document.get("prompt", "")
    battery = battery_brief(prompt, values.get("input_source", ""))
    questions = []

    def ask(field, title, why, unit, minimum=None, maximum=None):
        if values.get(field) is None or bases.get(field) not in {"confirmed", "stated"}:
            questions.append({"field": field, "title": title, "why": why, "unit": unit,
                              "value": values.get(field), "basis": bases.get(field, "assumed"),
                              "minimum": minimum, "maximum": maximum,
                              "options": [{"label": label, "value": value} for label, value in ANSWER_OPTIONS.get(field, [])]})

    if battery:
        ask("lifetime_hours", "How long should it run between charges or battery changes?",
            "Sets a runtime target. Sleep and alarm duty cycles still require a separate power analysis.", "hours", 0.01, 87600)
        ask("supply_capacity_mah", "What is the selected battery's rated capacity?",
            "Use the battery label or its datasheet. A generic cell capacity is not evidence for your battery.", "mAh", 0.01, 10000000)
    ask("temp_range", "What operating temperature range must the device tolerate?",
        "This is the environment you need, not the temperature rating of whichever parts search found.", "°C", -273, 300)
    ask("current_margin", "What current design margin should validation use?",
        "Confirm the allowance above the known load. It cannot compensate for missing current measurements.", "fraction", 0, 2)
    if re.search(r"\bcompact\b|\bsmall\b|\bwearable\b", prompt, re.I):
        ask("max_package_mm", "What is the maximum individual component package dimension?",
            "This checks component packages, not the completed PCB or enclosure size. Leave it unanswered if you have no component limit.", "mm", 0.01, 1000)

    tasks = []
    if battery and (re.search(r"\busb\b|charg|solar", prompt, re.I) or values.get("input_source") == "usb-5v+liion"):
        tasks.append({"id": "battery-modes", "kind": "engineering", "title": "Validate battery-only and charging operation separately",
            "detail": "This revision models one nominal power tree. A USB or charger voltage does not prove operation from a discharging battery. Review the battery voltage range, regulator dropout, charger connections, protection and load sharing against the circuit.",
            "subjects": [], "finding_ids": [], "fields": [], "action": "connections"})
    grouped = {}
    recipes = {
        "voltage_overlap": ("supply operating range", "Confirm the operating supply range from the exact part's datasheet. LEDs and transducers also need their driver circuit checked; do not substitute a guessed supply range."),
        "current_budget": ("current in operating modes", "Collect load currents at the intended voltage and operating modes, plus converter efficiency. Missing draw is unknown, not zero."),
        "thermal_dissipation": ("load, efficiency and thermal conditions", "Establish the load and conversion losses, then check package and PCB thermal conditions."),
        "pin_budget": ("usable GPIO and assigned pins", "Verify usable GPIO and the pins allocated to each function. Package pin count is not usable GPIO."),
        "interface_role_match": ("interface and role", "Verify the intended signal connection and interface support. A charger or discrete component may have no digital bus."),
        "temperature_rating": ("operating temperature rating", "Find the exact device and package's operating rating; absolute maximum ratings are not operating conditions."),
        "footprint": ("package dimensions", "Check package dimensions and the actual footprint against the manufacturer's drawing."),
        "availability": ("exact component and availability", "Resolve an exact manufacturer part and current supplier listing before ordering."),
        "data_integrity": ("conflicting or invalid specifications", "Compare the quoted manufacturer and supplier evidence for the exact component. Resolve source disagreements before using the disputed fields."),
        "energy_budget": ("battery and operating currents", "Confirm battery capacity, required runtime and currents. The engine does not model sleep/alarm duty cycles."),
    }
    for finding in document["report"]["findings"]:
        if finding["outcome"] != "UNKNOWN":
            continue
        rule, subject = finding["rule"], finding["subject"]
        engineering = rule in {"rail_coverage", "power_topology", "data_integrity"} and not (
            rule == "data_integrity" and finding.get("scope") == "research")
        kind = "engineering" if engineering else "evidence"
        key = (kind, subject)
        if key not in grouped:
            slot = board["slots"].get(subject, {})
            part = slot.get("part") or {}
            grouped[key] = {"id": f"{kind}-{subject}", "kind": kind,
                "title": part.get("mpn") or slot.get("label") or subject,
                "detail": "Review the power connections and unresolved model inputs." if engineering else "Resolve the missing specifications listed below using the exact component and its operating conditions.",
                "subjects": [subject], "finding_ids": [], "fields": [],
                "action": "connections" if engineering else "evidence" if part else "workspace", "steps": []}
        task = grouped[key]
        task["finding_ids"].append(finding["id"])
        field, step = recipes.get(rule, (rule.replace("_", " "), "Review this unchecked result against the circuit and supporting sources."))
        if field not in task["fields"]:
            task["fields"].append(field)
            if step not in task["steps"]:
                task["steps"].append(step)
    tasks.extend(grouped.values())
    counts = document["report"]["counts"]
    status = "conflicts" if counts["fail"] else "needs_review" if counts["unknown"] or counts["accepted"] or tasks or questions else "checks_complete"
    return {"version": 1, "status": status, "questions": questions, "tasks": tasks,
            "input_sources": [{"key": key, "label": supply.label} for key, supply in INPUT_SOURCES.items()],
            "note": "Passing checks apply only to the saved model and stated conditions. They are not certification of a build-ready circuit."}
