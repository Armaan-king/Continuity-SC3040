"""The power tree, and the graph the frontend draws from it.

## Which way the derivation runs

Rails are the source of truth; edges are the rendering. Not the other way round.

Deriving edges from rails is total: every rail with a source emits one power edge per
member, labelled with the rail id. Nothing can go wrong, and nothing needs parsing.

Deriving rails from edges is not total, and fails in three places:

1. **Edges are pairwise, rails are sets.** Three edges out of a regulator are one net
   carrying the sum, not three nets carrying a third each. Group them wrongly and each
   part is checked alone against the regulator's rating — every one passes, and a rail
   that is 40 mA over budget reports clean. It fails *silently*, which is the only kind
   of failure this engine cannot afford.
2. **`label` is a string, `voltage` is a number.** `"3V3"` → `3.3` works until someone
   writes `"3.3V"` or `"VDD_3V3"`.
3. **The board's input rail has no edge at all.** USB-C feeding the regulator has no
   source *slot*, so there is no edge to derive it from — and without that rail R5
   cannot find the regulator's input voltage and goes quiet on every board.

Going rails → edges makes all three disappear, and costs no contract change: the wire
format still carries exactly the `slots` and `edges` the frontend already renders.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Mapping, Sequence

from ..engine.models import (
    Board,
    Edge,
    PartSpec,
    Rail,
    Requirements,
    Slot,
    slots_without_a_rail,
)

INPUT_RAIL_ID = "VIN"

SUPPLY_NODE_ID = "__supply"
"""The board input, drawn as a node so what it feeds is not left floating.

It is **presentation only**: it travels on the `plan` event in its own `supply` field and
is referenced as the `from` of power edges, and it is never a slot. It is not in
`plan.slots`, never enters `state["slots"]`, and `pending` never contains it — so the
engine's "every declared slot ends resolved" property is untouched, which is precisely
what a first attempt at this got wrong by declaring it as a slot.

Without it, every part fed straight from the board input drew no edge at all and sat
unconnected on screen: on a solar board that was the charge controller and the battery
holder, which are exactly the parts a reader needs to see wired up. The double underscore
keeps it out of the id space a planner can name.
"""


@dataclass(frozen=True)
class PowerSource:
    """What the board is fed from, before any part is chosen.

    `basis` is where the numbers came from, and it travels with them. Every verdict
    that rests on this supply can therefore say whether it was checked against a
    published standard or against something read out of the user's sentence.
    """

    voltage: float
    i_limit: float | None
    label: str
    basis: str = "specification"

    capacity_mah: float | None = None
    """Charge the cell holds, for supplies that hold a finite amount of it.

    `None` for anything fed from a wall or a panel — a lifetime question does not arise
    there, and a capacity invented for one would make it look like it had been answered.
    Cells in series share a capacity rather than summing it, so two AA cells carry one
    cell's figure at twice the voltage.
    """

    @property
    def is_inferred(self) -> bool:
        return self.basis.startswith(INFERRED_BASIS)


INFERRED_BASIS = "read from your brief — not a standard supply"
"""Marks a supply the planner proposed rather than looked up.

## Why the model may propose these but not simply assert them

`voltage` is an operand of R1 and R5; `i_limit` is an operand of R4. A model that sets
them is deciding verdicts, and it can decide them differently on two runs of the same
prompt — which would move a board from pass to fail between rehearsal and stage.

So the division is the same one used everywhere else in this system: **the model
classifies into a vocabulary the engine owns; it does not supply operands.** Naming
`"usb-5v"` is a classification — enumerable, checkable, and visibly wrong when wrong.
Emitting `1.5` is an assertion — unbounded, unverifiable, silently wrong.

A fixed vocabulary alone is too brittle, though: eleven entries will never cover "two
18650s in series". So when nothing fits, the planner may propose a supply — and it
arrives marked `INFERRED_BASIS`, which is what a clarifying question is raised from.
The user confirms the number; the engine still owns it.
"""

INFERRED_BATTERY_LIMIT_BASIS = (
    f"{INFERRED_BASIS}; current limit carried from the battery classification, "
    "not a matching catalogue entry"
)
"""A stated pack voltage with a limit supplied only by its classified cell chemistry."""

BATTERY_SOURCES = frozenset({"battery-3v0", "battery-3v7", "battery-aa", "9v-battery"})


def proposed_source(voltage: float, i_limit: float, label: str) -> PowerSource:
    """A supply the planner read from the brief. Always confirm before relying on it."""
    return PowerSource(voltage=voltage, i_limit=i_limit, label=label, basis=INFERRED_BASIS)


INPUT_SOURCES: dict[str, PowerSource] = {
    # 1.5 A is what a USB-C source advertises by default via its Rp pull-up. 3 A needs
    # either a 3 A advertisement or a PD negotiation, so assuming it would hand every
    # board a current budget it has not actually been granted.
    "usb-5v": PowerSource(5.0, 1.5, "USB-C 5V", "USB Type-C default Rp advertisement"),
    "usb-5v-pd": PowerSource(5.0, 3.0, "USB-C 5V (PD)", "USB PD negotiated 5V/3A"),
    "5v-external": PowerSource(5.0, 2.0, "External 5V supply"),
    # Two supplies into one rail, through a mux or a charger. Modelled at the *higher*
    # voltage: a linear regulator's dissipation is worst on the higher input, so
    # sizing against the battery would pass a board that cooks on USB.
    # Capacity is left unset here: this supply is mains-backed, so how long it lasts is
    # not a question the board has to answer.
    "usb-5v+liion": PowerSource(5.0, 1.5, "USB-C 5V / Li-ion"),
    "battery-3v7": PowerSource(3.7, 2.0, "Li-ion 3.7V", capacity_mah=2000.0),
    # A CR2032 sustains ~0.2 mA and pulses to ~20 mA. The pulse figure is the ceiling
    # a current budget should check against; anything larger passes boards that flatten
    # the cell in an afternoon.
    "battery-3v0": PowerSource(
        3.0, 0.020, "Coin cell 3V", "CR2032 pulse discharge rating", capacity_mah=225.0
    ),
    "battery-aa": PowerSource(3.0, 1.0, "2x AA alkaline", capacity_mah=2500.0),
    "9v-battery": PowerSource(9.0, 0.5, "9V battery", capacity_mah=550.0),
    # A panel's *voltage* is a product class — 6 V and 12 V panels are sold as such, the
    # same way a 9 V battery is. Its *current* is not: it depends on the panel's area and
    # on the sun, and no number here could be checked against anything. So the limit is
    # None, which R4 already reports honestly as an unchecked budget rather than inventing
    # a ceiling. This is what lets a solar board name what feeds its charge controller.
    "solar-6v": PowerSource(6.0, None, "6V solar panel", "nominal panel rating; current depends on the panel"),
    "solar-12v": PowerSource(12.0, None, "12V solar panel", "nominal panel rating; current depends on the panel"),
    "solar-18v": PowerSource(18.0, None, "18V solar panel", "nominal panel rating; current depends on the panel"),
    "12v-barrel": PowerSource(12.0, 2.0, "12V barrel jack"),
    "24v-industrial": PowerSource(24.0, 2.0, "24V industrial supply"),
    "poe": PowerSource(48.0, 0.35, "PoE 802.3af", "IEEE 802.3af class 3"),
}


def source_named(said: str) -> str | None:
    """The `INPUT_SOURCES` key a user's sentence names, or None."""
    folded = said.casefold()
    names = [
        (name.casefold(), key)
        for key, source in INPUT_SOURCES.items()
        for name in (key, source.label)
    ]
    # Longer labels win before their prefixes: “USB-C 5V (PD)” must not be mistaken for
    # “USB-C 5V”, because choosing the wrong supply changes the board's current budget.
    for name, key in sorted(names, key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", folded):
            return key

    concise = re.search(r"(?<!\w)(\d+(?:\.\d+)?)\s*v\s+supply(?!\w)", folded)
    if concise is None:
        return None
    voltage = float(concise.group(1))
    candidates = [key for key, source in INPUT_SOURCES.items() if source.voltage == voltage]
    # “24 V supply” omits “industrial”, but it still names one and only one vocabulary
    # entry. Ambiguous voltages such as 5 V remain guidance rather than a redesign.
    return candidates[0] if len(candidates) == 1 else None


SUPPLY_TOLERANCE = 0.10
"""How far a stated voltage may sit from a catalogue entry and still be that entry.

Wide enough to absorb nominal-versus-actual wording (a coin cell called 3.0 V or 3.05 V,
AA cells called 3.0 V or 3.2 V), narrow enough that the failures this exists to catch —
48 V read as 24 V, 7.4 V read as 3.7 V, both exactly 2x — fall well outside it.
"""


class UnknownPowerSource(LookupError):
    """Raised rather than guessing what a board is plugged into.

    Substituting a default here is not a small inaccuracy, it inverts verdicts. A
    board fed from 12 V, silently modelled at 5 V, makes R1 report *pass* on a
    regulator rated to 6 V — the engine green-lighting a part that the input voltage
    would destroy — while R5 computes a third of the real dissipation.

    The graph turns this into a `question` and asks the user, which is what
    `interrupt()` is for. An unanswerable question is a legitimate outcome; a
    confident wrong answer is not.
    """

    def __init__(self, name: str) -> None:
        known = ", ".join(sorted(INPUT_SOURCES))
        super().__init__(
            f"unknown input source {name!r} — the board's supply voltage cannot be "
            f"assumed. Known sources: {known}."
        )
        self.name = name


def power_source(requirements: Requirements) -> PowerSource:
    """What the board is fed from. Raises rather than guessing.

    A voltage stated in the brief outranks the classification, because the classification
    is a lookup into a fixed vocabulary and a vocabulary that cannot express the user's
    supply rounds to the nearest thing it can name rather than refusing. Two of three §2
    briefs came out at exactly half their real voltage that way.

    The classification still supplies the *current* limit when the two agree on volts —
    a brief that says "12V barrel jack" tells us nothing about amps, and the catalogue
    does. Where they disagree, the stated voltage stands alone and the limit is unknown,
    which R4 already reports honestly as an unchecked budget.
    """
    classified = INPUT_SOURCES.get(requirements.input_source)
    stated = requirements.input_voltage

    if stated is None:
        if classified is None:
            raise UnknownPowerSource(requirements.input_source)
        return classified

    # Near enough is the catalogue's, because the catalogue also knows the *current*
    # limit and the brief almost never does. Losing a coin cell's 20 mA ceiling because
    # the brief said 3.05 V would disable R4 without saying so — the same silent loss
    # this whole path exists to prevent. The failures worth catching were 2x out.
    if classified is not None and abs(classified.voltage - stated) <= SUPPLY_TOLERANCE * stated:
        return classified

    # A named battery classification identifies the cell chemistry, so its catalogue
    # current ceiling remains relevant to a stated series-pack voltage. This is a
    # judgement for the user: buses and supplies named without an amp rating must not
    # gain one, but cells retain the limit that their classification establishes.
    if classified is not None and requirements.input_source in BATTERY_SOURCES:
        return PowerSource(
            voltage=stated,
            i_limit=classified.i_limit,
            label=f"{stated:g} V supply",
            basis=INFERRED_BATTERY_LIMIT_BASIS,
        )

    return PowerSource(
        voltage=stated,
        i_limit=None,
        label=f"{stated:g} V supply",
        basis=INFERRED_BASIS,
    )


# ── rails ─────────────────────────────────────────────────────────────────────


def assemble_rails(
    declared: Sequence[Rail],
    requirements: Requirements,
    supply: PowerSource | None = None,
    slot_ids: Sequence[str] = (),
) -> dict[str, Rail]:
    """The planner's declared rails, plus the board input rail it cannot know about.

    A regulator is a *member* of the rail feeding it and the *source* of the rail it
    makes. The planner declares the rails it creates; whichever regulators are left
    without an upstream rail are the ones hanging off the board input. With no declared
    rails, every slot is supplied directly by the board input.

    This is what makes a cascade work — 12 V → 5 V → 3V3. The 5 V regulator is already
    a member of the 12 V rail, so it does not also get attached to the input.

    The direct-drive branch keys on there being **no declared rails**, not on `unsupplied`
    coming out empty. Those are not the same condition: a malformed plan whose rails feed
    each other in a cycle also yields no unsupplied source, and quietly re-parenting every
    slot to the input would hide that rather than leave it visible.

    **A slot on no rail is left on no rail.** Attaching it to the input was tried on
    13 Aug and reverted the same hour: on the solar board it put the charge controller —
    whose input is the panel, not the battery — on a 3.7 V rail, and R1 duly reported that
    3.7 V is under its 4 V minimum. The board input is a *guess* at where an unmodelled
    part is fed from, and a guess that moves verdicts is exactly what this engine refuses
    to make. `unmodelled` draws them instead, as a connection that was never checked.
    """
    rails = {rail.id: rail for rail in declared}
    supplied = {slot_id for rail in declared for slot_id in rail.members}
    unsupplied = tuple(
        rail.source for rail in declared if rail.source and rail.source not in supplied
    )

    source = supply if supply is not None else power_source(requirements)
    rails[INPUT_RAIL_ID] = Rail(
        id=INPUT_RAIL_ID,
        voltage=source.voltage,
        source=None,
        members=unsupplied if declared else tuple(slot_ids),
        i_limit=source.i_limit,
        basis=source.basis,
    )
    return rails


def build_board(
    slots: Mapping[str, Slot],
    declared: Sequence[Rail],
    requirements: Requirements,
    supply: PowerSource | None = None,
) -> Board:
    return Board(
        requirements=requirements,
        slots=dict(slots),
        rails=assemble_rails(declared, requirements, supply, tuple(slots)),
    )


# ── edges, for the wire ───────────────────────────────────────────────────────


def capacity_of(source_key: str) -> float | None:
    """Charge held by a named supply, or `None` when it is not a finite one.

    The engine imports nothing, so this is read on the planner's side and travels down on
    `Requirements`, the same route `input_voltage` takes.
    """
    supply = INPUT_SOURCES.get(source_key)
    return supply.capacity_mah if supply is not None else None


def supply_node(source: PowerSource) -> dict[str, object]:
    """The board input, shaped for the `plan` event's `supply` field.

    Built here rather than in `api/events.py` so that the one place that knows what a
    supply is also owns how it is named on the wire.
    """
    return {"id": SUPPLY_NODE_ID, "label": source.label, "voltage": source.voltage}


def power_edges(rails: Mapping[str, Rail]) -> list[Edge]:
    """One edge per rail member. Ids are stable so `selection` can patch them by id.

    A rail with no source slot is the board input, and it draws from `SUPPLY_NODE_ID`
    rather than not drawing at all. It used to emit nothing, on the reasoning that there
    was no *node* to draw from — which was true, and left every part fed straight from
    the input floating unconnected on screen. The supply is that node; see its docstring
    for why it is not a slot.

    Declared rails are iterated before the input rail, so `seen` gives a part its real
    regulator when it is on both. A cascade therefore still reads left to right, with only
    the topmost regulator hanging off the supply.
    """
    edges: list[Edge] = []
    seen: set[str] = set()
    for rail in rails.values():
        source = rail.source or SUPPLY_NODE_ID
        for member in rail.members:
            if member in seen:
                continue
            seen.add(member)
            edges.append(
                Edge(
                    id=f"pwr-{member}",
                    source=source,
                    target=member,
                    label=rail.id,
                    kind="power",
                    status="pending",
                )
            )
    return edges


UNCHECKED = "unchecked"
"""Edge status for a connection drawn but never evaluated. See `unmodelled`."""


def unmodelled(slots: Mapping[str, Slot], rails: Mapping[str, Rail]) -> list[Edge]:
    """Stub edges for slots that belong to no rail, so nothing renders floating.

    These are the parts a plan never placed on the power tree: on a solar board, the
    charge controller and the battery holder. They are on the board and they are supplied
    by *something* — but by what is not modelled, so no rule has run on the connection and
    the edge says exactly that rather than borrowing the input rail's label.

    Drawing nothing was the previous behaviour and it read as a rendering fault. Drawing a
    `VIN` edge would read as a checked connection to the board input, which is the claim
    that made R1 fail a solar charger against a battery voltage. `UNCHECKED` is the only
    one of the three that is true.

    This is honest, not right. The fix is upstream: the planner declaring the rails those
    parts are actually on, at which point they stop being unmodelled and every rule picks
    them up with no change here. That needs a supply vocabulary entry for the source and a
    battery that can be both a source and a load.
    """
    return [
        Edge(
            id=f"pwr-{slot_id}",
            source=SUPPLY_NODE_ID,
            target=slot_id,
            label=None,
            kind="power",
            status=UNCHECKED,
        )
        # The same predicate the `rail_coverage` rule reports on, so the wire the reader
        # sees drawn as unchecked is exactly the part the trace says was not checked.
        for slot_id in slots_without_a_rail(slots, rails)
    ]


def data_edges(slots: Mapping[str, Slot]) -> list[Edge]:
    """Master → peripheral, one per peripheral, labelled once the bus is decided.

    `label` stays `None` until parts exist to resolve it — which bus a sensor lands on
    genuinely depends on what gets chosen. Power labels are never null, because the
    rail voltage is a design decision made before any part is picked.
    """
    masters = [(sid, s.part) for sid, s in slots.items() if _role(s) == "master"]
    if not masters:
        return []
    edges: list[Edge] = []
    for slot_id, slot in slots.items():
        if _role(slot) != "peripheral":
            continue
        # Explicit plan-time links already draw their chosen master through
        # `planned_data_edges`; once parts exist, infer the matching master for every
        # remaining peripheral. Preserve the former first-master edge when no bus can
        # be resolved so the graph remains visible while selection asks what is missing.
        master_id, master = next(
            (
                (candidate_id, candidate)
                for candidate_id, candidate in masters
                if _shared_bus(candidate, slot.part) is not None
            ),
            masters[0],
        )
        edges.append(
            Edge(
                id=f"bus-{slot_id}",
                source=master_id,
                target=slot_id,
                label=_shared_bus(master, slot.part),
                kind="data",
                status="pending",
            )
        )
    return edges


def planned_data_edges(links: Sequence[tuple[str, str]]) -> list[Edge]:
    """Data edges at plan time, before any part exists to say which bus they use.

    The planner knows the sensor will talk to the controller, and an explicit link
    owns that controller choice; it cannot yet know whether over I2C or SPI, because
    that depends on parts nobody has chosen. So the edge is declared with `label=None`
    and the `selection` that resolves the bus patches it — which is exactly what the
    contract says a null label means.

    **The id keys on the peripheral, not on the pair.** Each peripheral has exactly one
    data edge, and which master drives it can change between plan time and selection —
    the plan takes the master from the declared link, while `data_edges` infers the one
    that actually offers the peripheral's bus. Keying on the pair meant a two-master board
    declared `bus-mcu-sensor` and then patched `bus-mcu2-sensor`, so the patch landed on
    an id the client had never seen and the declared edge stayed `pending` for ever. Edge
    patches merge by id and have no delete, so the id must not encode anything mutable.
    `power_edges` was changed to `pwr-{member}` on 10 Aug for the same reason.

    Ids match `data_edges`, so the patch lands on the edge the plan drew.
    """
    owner: dict[str, str] = {}
    for master, peripheral in links:
        # One edge per peripheral, first link wins — the same rule `power_edges` uses
        # for a slot listed on two rails. Order of first appearance is preserved.
        owner.setdefault(peripheral, master)

    return [
        Edge(id=f"bus-{peripheral}", source=master, target=peripheral,
             label=None, kind="data", status="pending")
        for peripheral, master in owner.items()
    ]


def _role(slot: Slot) -> str | None:
    return slot.part.role if slot.part else None


def _shared_bus(master: PartSpec | None, peripheral: PartSpec | None) -> str | None:
    if master is None or peripheral is None:
        return None
    shared = [bus for bus in peripheral.interfaces if bus in master.interfaces]
    return shared[0] if shared else None


def graph_edges(board: Board) -> list[Edge]:
    """Every edge the `plan` event carries, power first so the graph reads left to right."""
    return (
        power_edges(board.rails)
        + unmodelled(board.slots, board.rails)
        + data_edges(board.slots)
    )


def resolved_edges(board: Board, verdicts: Sequence) -> list[Edge]:
    """Edges with `status` set from the verdicts touching them — a patch, not a replacement.

    A power edge carries the status of its rail's worst verdict; a data edge that of
    the bus check between its two ends. Anything unmentioned stays `pending`.
    """
    # Only a hard failure colours an edge. `Edge.status` has no warn state, and mapping
    # a warn to `pending` would be worse than losing it: pending means "not yet
    # determined", so a board that ends with warnings — which is most boards — would
    # show edges that never resolve and a pending count that never reaches zero. The
    # warning is not lost; it travels on the `check` event, which does have a warn
    # status, and shows up against the rule rather than against the wire.
    worst: dict[str, str] = {}
    for verdict in verdicts:
        if verdict.status != "fail":
            continue
        for slot_id in verdict.involved:
            key = f"{verdict.scope}:{slot_id}" if verdict.scope else slot_id
            worst[key] = "conflict"

    edges = []
    for edge in graph_edges(board):
        # An unmodelled connection stays unmodelled however the board turns out: no rule
        # ran on it, so a clean board must not upgrade it to `pass`.
        if edge.status == UNCHECKED:
            edges.append(edge)
            continue

        status = "pass"
        if edge.kind == "power" and f"{edge.label}:{edge.target}" in worst:
            status = worst[f"{edge.label}:{edge.target}"]
        elif edge.kind == "data" and edge.target in worst:
            status = worst[edge.target]
        edges.append(replace(edge, status=status))
    return edges
