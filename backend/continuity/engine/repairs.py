"""Deterministic repair constraints derived from the failing board, without an API."""
from __future__ import annotations

from . import draw, policy
from .models import Board, Repair


def propose(board: Board, resolution: policy.Resolution, alternatives) -> Repair | None:
    if not resolution.legal:
        return None
    conflict = resolution.conflict
    # Target the actual failed component when legal. The policy still validates the action.
    key = conflict.subject if conflict.subject in resolution.legal else resolution.legal[0]
    part = board.part(key)
    if part is None:
        return None
    output = board.rails.get(conflict.scope)
    if output is None or output.source != key:
        output = next((r for r in board.rails.values() if r.source == key), None)
    upstream = board.input_rail(key)
    constraint = {}
    if upstream:
        constraint["vin_min"] = upstream.voltage
    if output:
        constraint["vout"] = output.voltage
        load, _ = draw.rail_draw(board, draw.consumers(board, output))
        if load > 0:
            constraint["i_out_min"] = load * (1 + board.requirements.current_margin)

    if conflict.rule == "temperature_rating":
        constraint = {"rated_from": board.requirements.temp_range[0], "rated_to": board.requirements.temp_range[1]}
    elif conflict.rule in {"thermal_dissipation", "voltage_overlap"} and output and upstream:
        topology = "boost" if upstream.voltage < output.voltage else "buck" if upstream.voltage > output.voltage else None
        impossible_direction = (part.topology == "buck" and topology == "boost"
                                or part.topology == "boost" and topology == "buck"
                                or part.regulation == "linear" and upstream.voltage <= output.voltage)
        if topology and (impossible_direction or conflict.rule == "thermal_dissipation" and part.regulation == "linear"):
            if "change_topology" not in policy.allowed_actions(board, key):
                return Repair(key, "escalate", "The required topology change affects a protected component; review the supply or component requirement.")
            constraint["topology"] = topology
            return Repair(key, "change_topology",
                f"Use a {topology} converter for the declared {upstream.voltage:g} V input and {output.voltage:g} V rail. "
                "The replacement will be checked against the actual load and remaining board requirements.", constraint)
    if conflict.rule in {"voltage_overlap", "current_budget", "temperature_rating"} and constraint:
        return Repair(key, "swap", "Search for a replacement meeting the failing voltage, current or temperature requirement, then recheck the whole board.", constraint)
    remaining = alternatives.get(key, ())
    if remaining:
        return Repair(key, "swap", f"Evaluate the remaining candidates for {board.slots[key].label} against the board.")
    return Repair(key, "escalate", f"No supported repair remains for {conflict.rule.replace('_', ' ')}. Review the requirement or provide another component.")
