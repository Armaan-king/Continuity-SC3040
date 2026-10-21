"""Turns a brief into a board the engine can check.

The engine answers *what is broken*. This package answers *what is on the table* —
which slots exist, what rails they hang off, what the board is plugged into. That is
interpretation, not adjudication, and keeping the two in separate packages is what
makes "no compatibility verdict comes from a model" a structural claim rather than a
promise. `engine/` imports nothing from here.
"""

from .topology import (
    INPUT_SOURCES,
    PowerSource,
    UnknownPowerSource,
    assemble_rails,
    build_board,
    graph_edges,
    power_source,
)

__all__ = [
    "INPUT_SOURCES",
    "PowerSource",
    "UnknownPowerSource",
    "assemble_rails",
    "build_board",
    "graph_edges",
    "power_source",
]
