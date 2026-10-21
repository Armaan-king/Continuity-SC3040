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
    board: Board, consumers: list[tuple[str, PartSpec]], seen: frozenset[str] = frozenset()
) -> tuple[float, list[str]]:
    total = 0.0
    unstated: list[str] = []
    for slot_id, part in consumers:
        draw = consumer_draw(board, slot_id, part, seen)
        if draw is None:
            unstated.append(slot_id)
        else:
            total += draw
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
    if slot_id in seen:
        return None

    # `reflected_draw` returns None for two unrelated reasons, and they must not be
    # treated alike: *this part sources no rail* (so its own datasheet figure is the
    # answer) and *what it feeds is unknown* (so nothing here is knowable). Falling back
    # to `part.draw` in the second case reports a regulator's quiescent current as the
    # whole draw on its input rail — a few hundred microamps standing in for an entire
    # board — and the rail then passes its budget with confidence.
    #
    # Measured on a coin-cell beacon: the MCU published no current, so the 3V3 rail was
    # honestly unchecked, and the 20 mA cell above it reported `0 mA of 20 mA (0%)` and
    # passed. The one number that brief was about.
    if board.sources_a_rail(slot_id):
        return reflected_draw(board, slot_id, part, seen | {slot_id})
    return part.draw


def reflected_draw(
    board: Board, slot_id: str, part: PartSpec, seen: frozenset[str]
) -> float | None:
    """Input current a downstream regulator pulls, from what its output rail carries.

    Linear parts pass output current straight through — a 700 mA load on an LDO draws
    700 mA from its input, which is exactly why the difference becomes heat. Switchers
    trade current for voltage, so the input figure is `(Vout × I) / (η × Vin)`.
    """
    downstream = next((r for r in board.rails.values() if r.source == slot_id), None)
    if downstream is None:
        return None

    total, unstated = rail_draw(board, consumers(board, downstream), seen)
    if unstated:
        return None

    if not part.is_switching:
        return total

    # The output voltage is the rail the regulator makes, not a figure on its datasheet —
    # an adjustable part's range maximum here overstated the input current several-fold.
    input_rail = board.input_rail(slot_id)
    if input_rail is None or input_rail.voltage <= 0:
        return None
    return (downstream.voltage * total) / (part.conversion_efficiency * input_rail.voltage)


def rail_limit(board: Board, rail: Rail) -> tuple[float | None, PartSpec | None]:
    if rail.source and board.placed(rail.source):
        source = board.part(rail.source)
        return source.i_max, source  # type: ignore[union-attr]
    return rail.i_limit, None


def current_subject(board: Board, rail: Rail, consumers: list[tuple[str, PartSpec]]) -> str:
    if rail.source:
        return rail.source
    return max(consumers, key=lambda pair: pair[1].draw or 0.0)[0]
