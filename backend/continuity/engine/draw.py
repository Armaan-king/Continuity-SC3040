"""Rail current: who draws what, and what a regulator pulls from the rail above it.

Extracted from `rules` because it is one idea rather than a rule: the current on a net
is a property of the board's topology, and both R4 (budget) and R5 (heat) ask for it.

The subtle part is a regulator's draw on its *input* rail. That figure is not on any
datasheet — a regulator states its quiescent current, which is microamps, while a whole
board hangs off its output. The real answer is the output rail's draw reflected back
through the conversion, and computing it is what makes an input-rail budget mean
anything at all.
"""

from __future__ import annotations

from .models import Board, PartSpec, Rail

def consumers(board: Board, rail: Rail) -> list[tuple[str, PartSpec]]:
    return [(s, board.part(s)) for s in rail.members if board.placed(s)]  # type: ignore[misc]


def rail_draw(
    board: Board, consumers: list[tuple[str, PartSpec]], seen: frozenset[str] = frozenset(), *, ideal: bool = False
) -> tuple[float, list[str]]:
    total = 0.0
    unstated: list[str] = []
    for slot_id, part in consumers:
        draw, incomplete = consumer_estimate(board, slot_id, part, seen, ideal=ideal)
        total += draw
        if incomplete:
            unstated.append(slot_id)
    return total, unstated


def consumer_draw(
    board: Board, slot_id: str, part: PartSpec, seen: frozenset[str]
) -> float | None:
    """What one slot pulls from the rail it sits on.

    A regulator is checked for reflected draw *before* its own datasheet current,
    because the figure a regulator states is its quiescent current — using that would
    put a few hundred microamps on the input rail while a whole board hangs off its
    output.
    """
    total, incomplete = consumer_estimate(board, slot_id, part, seen)
    return None if incomplete else total


def consumer_estimate(
    board: Board, slot_id: str, part: PartSpec, seen: frozenset[str] = frozenset(), *, ideal: bool = False
) -> tuple[float, bool]:
    """Preserve the known load through a partial power tree; never count a load twice."""
    if slot_id in seen:
        return 0.0, True
    downstream = [rail for rail in board.rails.values() if rail.source == slot_id]
    if not downstream:
        return (part.draw or 0.0) * board.slots[slot_id].quantity, part.draw is None
    if part.regulation is None:
        return 0.0, True
    upstream = board.input_rail(slot_id)
    if part.is_switching and (upstream is None or upstream.voltage <= 0):
        return 0.0, True
    total, incomplete = 0.0, board.slots[slot_id].quantity != 1
    for rail in downstream:
        load, missing = rail_draw(board, consumers(board, rail), seen | {slot_id}, ideal=ideal)
        incomplete |= bool(missing) or any(not board.placed(key) for key in rail.members)
        if part.is_switching:
            eta = 1.0 if ideal and part.efficiency is None else part.conversion_efficiency
            load *= rail.voltage / (eta * upstream.voltage)
        total += load
    return total, incomplete



def reflected_draw(
    board: Board, slot_id: str, part: PartSpec, seen: frozenset[str]
) -> float | None:
    """Input current a downstream regulator pulls, from what its output rail carries.

    Linear parts pass output current straight through — a 700 mA load on an LDO draws
    700 mA from its input, which is exactly why the difference becomes heat. Switchers
    trade current for voltage, so the input figure is `(Vout × I) / (η × Vin)`.
    """
    if not board.sources_a_rail(slot_id):
        return None
    total, incomplete = consumer_estimate(board, slot_id, part, seen - {slot_id})
    return None if incomplete else total


def dependencies(board: Board, slots) -> tuple[str, ...]:
    """All loads contributing to an upstream calculation, including cascaded rails."""
    found = set(slots)
    pending = list(slots)
    while pending:
        source = pending.pop()
        for rail in board.rails.values():
            if rail.source == source:
                for member in rail.members:
                    if member not in found:
                        found.add(member)
                        pending.append(member)
    return tuple(sorted(found))


def rail_limit(board: Board, rail: Rail) -> tuple[float | None, PartSpec | None]:
    if rail.source and board.placed(rail.source):
        source = board.part(rail.source)
        return source.i_max, source  # type: ignore[union-attr]
    return rail.i_limit, None


def current_subject(board: Board, rail: Rail, consumers: list[tuple[str, PartSpec]]) -> str:
    if rail.source:
        return rail.source
    return max(consumers, key=lambda pair: pair[1].draw or 0.0)[0]
