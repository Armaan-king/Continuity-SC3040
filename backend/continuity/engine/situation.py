"""Stable categorical descriptions of conflicts for safely retrieving prior repairs."""

from __future__ import annotations

import math

from . import draw, packages, regulation
from .models import Board, Verdict

ELECTRICAL_RULES = frozenset({"thermal_dissipation", "current_budget", "efficiency"})
"""Rules whose own physics depends on the supply drop and output load."""

DROP_BANDS = ((1.0, "<1V"), (3.0, "1-3V"), (8.0, "3-8V"), (math.inf, ">=8V"))
"""Voltage-drop buckets use strict upper bounds so an exact boundary enters the next band."""

CURRENT_BANDS = (
    (0.1, "<100mA"),
    (0.5, "100-500mA"),
    (2.0, "500mA-2A"),
    (math.inf, ">=2A"),
)
"""Load-current buckets use strict upper bounds so an exact boundary enters the next band."""


def _band(value: float, bands: tuple[tuple[float, str], ...]) -> str | None:
    """The first named strict upper-bound band containing `value`."""
    return next((label for threshold, label in bands if value < threshold), None)


def _supplied_rail(board: Board, slot_id: str):
    """The engine-owned rail whose source is the subject slot."""
    return next((rail for rail in board.rails.values() if rail.source == slot_id), None)


def signature(conflict: Verdict, board: Board, *, category: str | None = None) -> str | None:
    """The shape of this conflict, as engine-owned categorical tokens.

    `category` is our own name for the kind of part — `parts.categories.canonical`, which
    the caller resolves because this module may not import `parts`. It is deliberately
    not `part.category`: that is the distributor's wording, and keying a precedent on a
    vendor's taxonomy means a re-titled category stops matching without anything failing.
    """
    slot = board.slots.get(conflict.subject)
    if slot is None or slot.part is None:
        return None

    part = slot.part
    components = [conflict.rule]
    if category:
        components.append(category)
    regulation_class = regulation.regulation_from_topology(part.topology) or regulation.regulation_from_category(
        part.category
    )
    if regulation_class is not None:
        components.append(regulation_class)
    if package_family := packages.family(part.package):
        components.append(f"pkg:{package_family}")

    if conflict.rule in ELECTRICAL_RULES:
        input_rail = board.input_rail(conflict.subject)
        supplied_rail = _supplied_rail(board, conflict.subject)
        if input_rail is not None and supplied_rail is not None:
            drop = _band(input_rail.voltage - supplied_rail.voltage, DROP_BANDS)
            if drop is not None:
                components.append(f"drop:{drop}")
            load, unstated = draw.rail_draw(board, draw.consumers(board, supplied_rail))
            if not unstated:
                current = _band(load, CURRENT_BANDS)
                if current is not None:
                    components.append(f"load:{current}")

    return "|".join(components) if len(components) >= 3 else None
