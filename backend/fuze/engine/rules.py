"""Deterministic electrical and sourcing checks with explicit uncertainty.

R1–R5 are electrical. R6 is sourcing, and it flows through the identical resolution
loop, which is the architectural claim: one loop handling a sold-out part and an
overheating regulator the same way.

Every rule is a pure function `Board -> list[Verdict]`. No I/O, no LLM, no clock. A
rule may only compare fields that were fetched, and every verdict it returns carries
the field name, its verbatim value and where that value came from.

A rule that *cannot* evaluate says `warn` and names the missing field. It never skips
quietly and it never substitutes a default — an unchecked constraint reported as a
pass is the one failure mode that would make the whole engine untrustworthy.
"""

from __future__ import annotations

from dataclasses import replace

from . import draw as rail_current
from . import efficiency
from . import format as fmt
from . import buses
from . import packages
from .models import (
    ASSUMED_EFFICIENCY,
    ASSUMED_EFFICIENCY_SOURCE,
    DOSSIER_SOURCE,
    is_dossier_source,
    Board,
    Evidence,
    PartSpec,
    Rail,
    Verdict,
)

# thresholds — every one of these is an advisory band, not a physical limit
DERATING_THRESHOLD = 0.80
"""Warn once a rail draws this fraction of its regulator's rating."""

THERMAL_RISE_WARN_C = 60.0
"""Warn on this much junction rise even when the absolute limit is met."""

MAX_EVIDENCE_ROWS = 4
"""Beyond this the drawer stops being readable."""

def efficiency_evidence(
    part: PartSpec, subject: str, band: tuple[float, float] | None
) -> tuple[Evidence, ...]:
    """One row saying whether efficiency came from the payload or an engine band."""
    if part.efficiency is not None:
        return part.cite(subject, "efficiency")
    if band is None:
        return ()
    low, high = band
    return (
        Evidence(
            subject,
            "efficiency (bounded)",
            f"{fmt.percent(low)}–{fmt.percent(high)}",
            f"{efficiency.BAND_SOURCE}, {efficiency.band_label(part)}",
        ),
    )


def _supply_evidence(rail: Rail, subject: str, *, current: bool = False) -> tuple[Evidence, ...]:
    """Cite where a rail's own numbers came from, when the rail is the operand.

    Silent for a rail fed by a regulator: its voltage comes from that part's datasheet
    and the verdict already quotes it. Only the board input rail carries a `basis`,
    because only its numbers come from outside the parts list — a published standard,
    or the user's own sentence. Those are not equally trustworthy and the screen should
    not present them as if they were.
    """
    if not rail.basis:
        return ()
    value = fmt.milliamps(rail.i_limit) if current and rail.i_limit is not None else fmt.volts(rail.voltage)
    label = f"{rail.id} supply {'current' if current else 'voltage'}"
    return (Evidence(subject, label, value, rail.basis),)


CURRENT_BASIS_SOURCE = "no duty cycle is published by any distributor"
"""Why R5 sizes heat from peak current rather than an average.

A radio's 500 mA peak is a transmit burst, and a package heats on average power, so
the physically exact figure would be `peak × duty cycle`. No distributor publishes a
duty cycle, and inventing one would be precisely the unsourced inference this engine
exists to avoid. Peak is the conservative reading and it is the one we can actually
cite, so that is what R5 uses — and it says so on screen rather than implying a
duty-cycle model it does not have.
"""


# ── R1 · voltage_overlap ──────────────────────────────────────────────────────


def voltage_overlap(board: Board) -> list[Verdict]:
    """Every part on a rail must tolerate it, and whatever sources a rail must make it.

    FAIL unless `part.vmin <= rail.voltage <= part.vmax` for each member, and unless the
    rail's source can actually be set to `rail.voltage`.
    """
    verdicts: list[Verdict] = []
    for rail in board.rails.values():
        for slot_id in rail.members:
            part = board.part(slot_id)
            if part is None:
                continue
            verdicts.append(_check_one_supply(board, rail, slot_id, part))

        if rail.source and board.placed(rail.source):
            verdicts.append(
                _check_source(board, rail, rail.source, board.part(rail.source))  # type: ignore[arg-type]
            )
    return verdicts


def _check_source(board: Board, rail: Rail, slot_id: str, part: PartSpec) -> Verdict:
    """Can the part feeding this rail actually produce it?

    Every regulator used to be checked only as a *load* — it is a member of the rail
    feeding it, and the source of the rail it makes, and only the first was examined. So
    nothing noticed an LDO asked to turn 3.0 V into 3.3 V, or a fixed 5 V buck declared
    as the source of a 3V3 rail. Two live battery boards shipped with `0 conflict`.
    """
    at = f"{rail.id} at {fmt.volts(rail.voltage)}"
    evidence = part.cite(slot_id, "vout_min", "vout_max")
    involved = (slot_id, *rail.members)

    def verdict(
        status: str, detail: str, extra: tuple[Evidence, ...] = (), *, unchecked: bool = False
    ) -> Verdict:
        return Verdict(
            rule="voltage_overlap",
            scope=rail.id,
            status=status,
            detail=detail,
            unchecked=unchecked,
            subject=slot_id,
            involved=involved,
            evidence=evidence + extra,
        )

    reaches = part.produces(rail.voltage)
    if reaches is None:
        return verdict(
            "warn",
            f"{part.mpn} states no output voltage — that it can supply {at} is unchecked.",
            unchecked=True,
        )
    if reaches is False:
        return verdict("fail", f"{part.mpn} outputs {_output_span(part)}; {at} is outside that.")

    # A linear regulator burns the difference as heat, so it cannot produce more than it
    # is given. Only asserted when the part says it is linear: a switcher that states no
    # topology must not be failed for something we cannot show it does.
    if part.regulation == "linear":
        upstream = board.input_rail(slot_id)
        if upstream is not None and upstream.voltage <= rail.voltage:
            return verdict(
                "fail",
                f"{part.mpn} is a linear regulator and cannot step up — "
                f"{at} is above its {fmt.volts(upstream.voltage)} input.",
                _supply_evidence(upstream, slot_id),
            )

    upstream = board.input_rail(slot_id)
    if upstream is None:
        return verdict("warn", f"{part.mpn} has no unambiguous input supply; conversion to {at} is unchecked.", unchecked=True)
    topology = (part.topology or "").strip().casefold()
    if topology == "buck" and upstream.voltage < rail.voltage:
        return verdict("fail", f"{part.mpn} is a buck converter and cannot raise {fmt.volts(upstream.voltage)} to {at}.",
                       part.cite(slot_id, "topology") + _supply_evidence(upstream, slot_id))
    if topology == "boost" and upstream.voltage > rail.voltage:
        return verdict("fail", f"{part.mpn} is a boost converter and cannot regulate {fmt.volts(upstream.voltage)} down to {at}.",
                       part.cite(slot_id, "topology") + _supply_evidence(upstream, slot_id))
    return verdict("pass", f"{part.mpn} supplies {_output_span(part)}, covering {at}.")


def _output_span(part: PartSpec) -> str:
    if part.vout_min == part.vout_max:
        return fmt.volts(part.vout_min)  # type: ignore[arg-type]
    return f"{fmt.volts(part.vout_min)}–{fmt.volts(part.vout_max)}"  # type: ignore[arg-type]


def _check_one_supply(board: Board, rail: Rail, slot_id: str, part: PartSpec) -> Verdict:
    """Check what can be checked.

    Real distributor data is one-sided far more often than it is complete: a regulator
    listing `"Voltage - Supply": "15V"` has stated a maximum and no minimum. Demanding
    both before evaluating anything would report *unchecked* on a part sitting at twice
    its rated voltage — a definite failure, silently downgraded to a shrug.

    So each bound is judged on its own. Either one being violated is a fail regardless
    of the other; a bound that is satisfied while the other is unknown is a warn that
    names what is missing rather than implying a clean pass.
    """
    involved = (slot_id, rail.source) if rail.source else (slot_id,)
    evidence = part.cite(slot_id, "vmin", "vmax") + _supply_evidence(rail, slot_id)
    at = f"{rail.id} at {fmt.volts(rail.voltage)}"

    def verdict(status: str, detail: str, *, unchecked: bool = False) -> Verdict:
        return Verdict(
            rule="voltage_overlap",
            scope=rail.id,
            status=status,
            detail=detail,
            unchecked=unchecked,
            subject=slot_id,
            involved=involved,
            evidence=evidence,
        )

    if part.vmax is not None and rail.voltage > part.vmax:
        return verdict(
            "fail",
            f"{part.mpn} is rated to {fmt.volts(part.vmax)} — {at} is above that.",
        )

    if part.vmin is not None and rail.voltage < part.vmin:
        return verdict(
            "fail",
            f"{part.mpn} needs at least {fmt.volts(part.vmin)} — {at} is below that.",
        )

    if part.vmin is not None and part.vmax is not None:
        span = f"{fmt.volts(part.vmin)}–{fmt.volts(part.vmax)}"
        return verdict("pass", f"{part.mpn} accepts {span}; {rail.id} is {fmt.volts(rail.voltage)}.")

    if part.vmax is not None:
        return verdict(
            "warn",
            f"{at} is within the {fmt.volts(part.vmax)} maximum {part.mpn} states, "
            f"but it publishes no minimum.",
            unchecked=True,
        )

    if part.vmin is not None:
        return verdict(
            "warn",
            f"{at} clears the {fmt.volts(part.vmin)} minimum {part.mpn} states, "
            f"but it publishes no maximum.",
            unchecked=True,
        )

    return verdict(
        "warn",
        f"{part.mpn} does not state a supply range — {at} could not be checked.",
        unchecked=True,
    )


# ── R2 · interface_role_match ─────────────────────────────────────────────────


def interface_role_match(board: Board) -> list[Verdict]:
    """A peripheral's bus must be offered by a master, with compatible roles.

    FAIL unless a bus is shared; FAIL if two parts drive the same bus as master;
    WARN if there are more SPI peripherals than free GPIO for their chip selects.
    """
    if board.signal_nets:
        from .signals import interfaces
        return interfaces(board)
    masters = _by_role(board, "master")
    peripherals = _by_role(board, "peripheral")
    verdicts: list[Verdict] = []

    # A board mid-placement is not a board with parts missing. R2 is the one rule whose
    # verdict depends on evaluation *order*: a peripheral placed before its controller
    # has no master to talk to, which is true at that instant and not a fault. Reporting
    # it as a definite failure sent whole runs into repair loops over nothing.
    incomplete = any(slot.part is None for slot in board.slots.values())

    verdicts.extend(_bus_contention(masters))
    # An unspecified controller bus must remain visible even before peripherals
    # are placed, so candidate ranking does not reward an empty specification.
    for master_id, master in masters:
        if not master.interfaces:
            verdicts.append(Verdict(
                "interface_role_match", "warn",
                f"{master.mpn}'s controller interfaces are not stated in the available specifications; "
                "verify its datasheet before checking signal compatibility.",
                master_id, unchecked=True, evidence=master.cite(master_id, "interfaces")))
    from .controllers import uncertain_controllers
    uncertain = uncertain_controllers(board) if not masters else ()
    for slot_id, part in peripherals:
        if uncertain:
            verdicts.append(Verdict(
                "interface_role_match", "warn",
                f"Controller role is unverified for {fmt.listing(list(uncertain))}; "
                f"{part.mpn}'s interface compatibility remains unknown.",
                slot_id, involved=(slot_id, *uncertain), unchecked=True,
                evidence=part.cite(slot_id, "interfaces")))
        else:
            verdicts.append(_check_one_bus(slot_id, part, masters, incomplete))
    verdicts.extend(_chip_select_pressure(masters, peripherals))
    verdicts.extend(_unplaceable_on_a_bus(board))
    return verdicts


def _by_role(board: Board, role: str) -> list[tuple[str, PartSpec]]:
    return [
        (slot_id, slot.part)
        for slot_id, slot in board.slots.items()
        if slot.part is not None and slot.part.role == role
    ]


def _unplaceable_on_a_bus(board: Board) -> list[Verdict]:
    """Parts that speak a bus but sit in neither role, so no other check can see them.

    `role` is a normalised field, which means a model fills it. `_by_role` collects only
    `master` and `peripheral`, so a part left `None` — or filed `passive` while still
    advertising interfaces — is not checked, not reported, simply absent. A rule the
    engine owns is switched off by a field the engine did not compute, which is the one
    arrangement this design exists to prevent.

    Measured 13 Aug on `CAN gateway bridging two buses, 12V automotive supply`, which
    passed 4/4 with no conflicts:

        mcu                ATMEGA328P-AU      role=master   ['I2C', 'SPI', 'UART']
        can_transceiver_1  UJA1075ATW/5V0/WD  role=None     ['LIN', 'CAN']
        can_transceiver_2  UJA1075ATW/5V0/WD  role=None     ['LIN', 'CAN']

    The ATmega328P has no CAN controller. Two CAN transceivers hung off a part that
    cannot drive them and R2 said nothing at all.

    **A warning, not a failure.** Which side the part sits on is unknown, so whether the
    board is wrong is also unknown — and a `fail` would start a repair loop against a
    question no replacement answers. Silence was the bug; a named warning is the fix.
    Parts with no interfaces are untouched: a regulator is legitimately `passive`, and
    there is nothing about it for this rule to check.
    """
    return [
        Verdict(
            rule="interface_role_match",
            status="warn",
            unchecked=True,
            detail=(
                f"{part.mpn} offers {fmt.listing(sorted({buses.canonical_bus(b) for b in part.interfaces}))} "
                f"but is filed as {part.role or 'no role'}, so it was not checked against "
                f"any controller on this board."
            ),
            subject=slot_id,
            involved=(slot_id,),
            evidence=tuple(part.cite(slot_id, "interfaces")),
        )
        for slot_id, slot in board.slots.items()
        if (part := slot.part) is not None
        and part.interfaces
        and part.role not in ("master", "peripheral")
    ]


def _bus_contention(masters: list[tuple[str, PartSpec]]) -> list[Verdict]:
    owners: dict[str, list[tuple[str, PartSpec]]] = {}
    for slot_id, part in masters:
        offered: set[str] = set()
        for bus in part.interfaces:
            canonical = buses.canonical_bus(bus)
            if canonical in offered:
                continue
            offered.add(canonical)
            owners.setdefault(canonical, []).append((slot_id, part))

    verdicts: list[Verdict] = []
    for bus, holders in owners.items():
        if len(holders) < 2:
            continue
        names = fmt.listing([p.mpn for _, p in holders])
        verdicts.append(
            Verdict(
                rule="interface_role_match",
                status="fail",
                detail=f"{names} both drive {bus} as master — only one may.",
                subject=holders[0][0],
                involved=tuple(slot_id for slot_id, _ in holders),
                evidence=tuple(
                    row for slot_id, part in holders for row in part.cite(slot_id, "interfaces")
                ),
            )
        )
    return verdicts


def _check_one_bus(
    slot_id: str,
    part: PartSpec,
    masters: list[tuple[str, PartSpec]],
    incomplete: bool = False,
) -> Verdict:
    evidence = part.cite(slot_id, "interfaces")
    involved = (slot_id, *(m_id for m_id, _ in masters))

    if not part.interfaces:
        return Verdict(
            rule="interface_role_match",
            status="warn",
            unchecked=True,
            detail=f"{part.mpn} does not state an interface — its bus could not be checked.",
            subject=slot_id,
            involved=involved,
            evidence=evidence,
        )

    if not masters:
        # Only a fault once the board is complete. Until then it is a slot still to come.
        return Verdict(
            rule="interface_role_match",
            status="warn" if incomplete else "fail",
            detail=(
                f"{part.mpn} needs {fmt.listing(list(part.interfaces))}; "
                f"no controller has been chosen yet."
                if incomplete
                else f"{part.mpn} needs {fmt.listing(list(part.interfaces))} "
                f"but the board has no controller to drive it."
            ),
            subject=slot_id,
            involved=involved,
            evidence=evidence,
        )

    for master_id, master in masters:
        for peripheral_bus in part.interfaces:
            for master_bus in master.interfaces:
                if not buses.master_satisfies_bus(peripheral_bus, master_bus):
                    continue
                detail = f"{part.mpn} on {peripheral_bus}, offered by {master.mpn}."
                if buses.canonical_bus(peripheral_bus) != buses.canonical_bus(master_bus):
                    detail = (
                        f"{part.mpn} on {peripheral_bus}, offered by {master.mpn} "
                        f"as {master_bus}."
                    )
                return Verdict(
                    rule="interface_role_match",
                    status="pass",
                    detail=detail,
                    subject=slot_id,
                    involved=(slot_id, master_id),
                    evidence=evidence + master.cite(master_id, "interfaces"),
                )

    unknown_masters = [master.mpn for _, master in masters if not master.interfaces]
    if unknown_masters:
        return Verdict(
            "interface_role_match", "warn",
            f"{part.mpn} needs {fmt.listing(list(part.interfaces))}, but controller interface data "
            f"is missing for {fmt.listing(unknown_masters)}. Compatibility is unknown; "
            "missing data does not establish an interface mismatch.",
            slot_id, involved=involved, unchecked=True,
            evidence=evidence + tuple(r for m_id, m in masters for r in m.cite(m_id, "interfaces")),
        )

    offered = fmt.listing(sorted({bus for _, m in masters for bus in m.interfaces}))
    return Verdict(
        rule="interface_role_match",
        status="fail",
        detail=(
            f"{part.mpn} speaks {fmt.listing(list(part.interfaces))}; "
            f"the controller offers {offered} — no shared bus."
        ),
        subject=slot_id,
        involved=involved,
        evidence=evidence + tuple(r for m_id, m in masters for r in m.cite(m_id, "interfaces")),
    )


def _chip_select_pressure(
    masters: list[tuple[str, PartSpec]], peripherals: list[tuple[str, PartSpec]]
) -> list[Verdict]:
    spi = [(s, p) for s, p in peripherals if any(buses.canonical_bus(bus) == "SPI" for bus in p.interfaces)]
    if not spi or not masters:
        return []
    master_id, master = masters[0]
    if master.pins_available is None:
        return []

    used = sum(p.pins_required or 0 for _, p in peripherals)
    free = master.pins_available - used
    if len(spi) <= free:
        return []
    return [
        Verdict(
            rule="interface_role_match",
            status="warn",
            detail=(
                f"{fmt.plural(len(spi), 'SPI peripheral')} need a chip select each, "
                f"but only {fmt.plural(free, 'GPIO')} remain on {master.mpn}."
            ),
            subject=master_id,
            involved=(master_id, *(s for s, _ in spi)),
            evidence=master.cite(master_id, "pins_available"),
        )
    ]


# ── R3 · pin_budget ───────────────────────────────────────────────────────────


BUS_PINS: dict[str, tuple[int, int]] = {
    # bus → (pins the bus costs once, pins each additional device costs)
    "I2C": (2, 0),  # SDA + SCL, shared by every device on the bus
    "SPI": (3, 1),  # SCK/MOSI/MISO shared, one chip select per device
    "UART": (0, 2),  # TX + RX, point to point — nothing is shared
    "CAN": (2, 0),  # TX + RX to one transceiver
    "1-WIRE": (1, 0),
}
"""What a bus costs a controller in pins.

R3 used to sum `pins_required` over peripherals. That field was populated on 4 of 33
live parts and all four were *masters*, so the sum was always zero and the failing
branch was unreachable — R3 reported "checks passed" having measured nothing.

Distributors do not publish a peripheral's pin count, but they do publish its bus, and a
bus has a known cost. Three I²C sensors are still two pins; three SPI devices are three
shared lines plus a chip select each. That is arithmetic the engine owns, over a field
that is actually present.
"""


def _pin_demand(consumers: list[tuple[str, PartSpec]]) -> tuple[int, list[str]]:
    """(GPIO the peripherals need, slots that state neither a count nor a bus).

    A stated `pins_required` always wins — it is a measurement, and the bus cost is an
    estimate. Everything else is priced by the bus it sits on, once for the bus and
    again per device where the bus needs it.
    """
    total = 0
    devices_per_bus: dict[str, int] = {}
    silent: list[str] = []

    for slot_id, part in consumers:
        if part.pins_required is not None:
            total += part.pins_required
            continue
        bus = next(
            (canonical for raw in part.interfaces if (canonical := buses.canonical_bus(raw)) in BUS_PINS),
            None,
        )
        if bus is None:
            silent.append(slot_id)
            continue
        devices_per_bus[bus] = devices_per_bus.get(bus, 0) + 1

    for bus, count in devices_per_bus.items():
        shared, per_device = BUS_PINS[bus]
        total += shared + per_device * count

    return total, silent


def pin_budget(board: Board) -> list[Verdict]:
    """FAIL if the peripherals ask for more GPIO than the controller offers."""
    if board.signal_nets:
        from .signals import pin_budgets
        return pin_budgets(board)
    masters = _by_role(board, "master")
    if not masters:
        return []
    if len(masters) > 1:
        return [Verdict("pin_budget", "warn",
            "Multiple controllers are present; declare signal nets to assign each peripheral and compute its controller's pin budget.",
            key, unchecked=True, evidence=part.cite(key, "pins_available")) for key, part in masters]
    master_id, master = masters[0]

    # Every peripheral counts, including the ones that state no pin requirement — they
    # are exactly what makes the budget a floor rather than a total, and filtering them
    # out here is what made them invisible to the check below.
    consumers = _by_role(board, "peripheral")
    involved = (master_id, *(slot_id for slot_id, _ in consumers))
    evidence = master.cite(master_id, "pins_available")

    if master.pins_available is None:
        return [
            Verdict(
                rule="pin_budget",
                status="warn",
                unchecked=True,
                detail=f"{master.mpn} does not state a GPIO count — pin budget unchecked.",
                subject=master_id,
                involved=involved,
                evidence=evidence,
            )
        ]

    required, silent = _pin_demand(consumers)
    detail_tail = (
        f"{fmt.plural(len(consumers), 'peripheral')} on {master.mpn} "
        f"({master.pins_available} GPIO)"
    )

    if required > master.pins_available:
        return [
            Verdict(
                rule="pin_budget",
                status="fail",
                detail=f"{detail_tail} need {required} GPIO — {required - master.pins_available} short.",
                subject=master_id,
                involved=involved,
                evidence=evidence
                + tuple(r for s, p in consumers for r in p.cite(s, "pins_required"))[
                    :MAX_EVIDENCE_ROWS
                ],
            )
        ]

    if silent:
        # Counting an unstated pin requirement as zero is how a full board reports
        # room to spare. Say it is a floor instead.
        names = fmt.listing([board.slots[s].label for s in silent])
        return [
            Verdict(
                rule="pin_budget",
                status="warn",
                unchecked=True,
                detail=(
                    f"At least {required} of {master.pins_available} GPIO used by "
                    f"{detail_tail} — {names} state no pin count."
                ),
                subject=master_id,
                involved=involved,
                evidence=evidence,
            )
        ]

    return [
        Verdict(
            rule="pin_budget",
            status="pass",
            detail=f"{required} of {master.pins_available} GPIO used by {detail_tail}.",
            subject=master_id,
            involved=involved,
            evidence=evidence,
        )
    ]


# ── R4 · current_budget ───────────────────────────────────────────────────────


def current_budget(board: Board) -> list[Verdict]:
    """Worst-case draw on a rail, with design margin, against what feeds it.

    `draw` sums every part's *peak*, i.e. it already assumes all peaks coincide.
    `current_margin` is headroom on top of that worst case — see `Requirements`.
    """
    verdicts: list[Verdict] = []
    for rail in board.rails.values():
        verdict = _check_rail_current(board, rail)
        if verdict is not None:
            verdicts.append(verdict)
    return verdicts



def _check_rail_current(board: Board, rail: Rail) -> Verdict | None:
    consumers = rail_current.consumers(board, rail)
    if not consumers:
        return None

    draw, unstated = rail_current.rail_draw(board, consumers)
    limit, source = rail_current.rail_limit(board, rail)
    subject = rail_current.current_subject(board, rail, consumers)
    involved = (subject, *(s for s, _ in consumers))
    supply = source.mpn if source else f"the {rail.id} supply"

    evidence: tuple[Evidence, ...] = ()
    if source and rail.source:
        evidence += source.cite(rail.source, "i_max")
    else:
        evidence += _supply_evidence(rail, subject, current=True)
    evidence += tuple(
        row
        for slot_id, part in sorted(consumers, key=lambda p: -(p[1].draw or 0.0))
        for row in part.cite(slot_id, "i_peak")
    )[:MAX_EVIDENCE_ROWS]

    evidence += tuple(Evidence(key, "quantity", str(board.slots[key].quantity), "Board component quantity")
                      for key in rail_current.dependencies(board, [s for s, _ in consumers])
                      if board.slots[key].quantity != 1)

    if limit is None:
        return Verdict(
            rule="current_budget",
            scope=rail.id,
            status="warn",
            unchecked=True,
            detail=f"{supply} does not state a current rating — {rail.id} budget unchecked.",
            subject=subject,
            involved=involved,
            evidence=evidence,
        )

    margin = board.requirements.current_margin
    required = draw * (1 + margin)
    converter_keys = rail_current.dependencies(board, [key for key, _ in consumers])
    assumed_conversion = [key for key in converter_keys if board.sources_a_rail(key)
                          and (part := board.part(key)) and part.is_switching and part.efficiency is None]
    if assumed_conversion:
        evidence += tuple(row for key in assumed_conversion
                          for row in efficiency_evidence(board.part(key), key, (ASSUMED_EFFICIENCY, ASSUMED_EFFICIENCY)))
        ideal_draw, _ = rail_current.rail_draw(board, consumers, ideal=True)
        if ideal_draw * (1 + margin) <= limit and not unstated:
            return Verdict("current_budget", "warn",
                f"Estimated {fmt.milliamps(required)} including margin on {rail.id}, against {fmt.milliamps(limit)}. "
                "Converter efficiency is assumed; the input-current budget requires measured efficiency at this load.",
                subject, involved=involved, evidence=evidence, scope=rail.id, unchecked=True)

    # A rail can be over budget on the parts that *did* state a draw. Waiting for
    # complete data before deciding would report "unchecked" on a board that is already
    # provably over — the unknown parts can only make it worse, never better.
    if unstated and required > limit:
        names = fmt.listing([board.part(s).mpn for s in unstated])  # type: ignore[union-attr]
        verb = "states" if len(unstated) == 1 else "state"
        return Verdict(
            rule="current_budget",
            scope=rail.id,
            status="fail",
            detail=(
                f"{fmt.milliamps(draw)} on {rail.id} plus {fmt.percent(margin)} margin "
                f"= {fmt.milliamps(required)}, above the {fmt.milliamps(limit)} rating of "
                f"{supply} — and that is a floor, because {names} {verb} no draw."
            ),
            subject=subject,
            involved=involved,
            evidence=evidence,
        )

    if unstated:
        names = fmt.listing([board.part(s).mpn for s in unstated])  # type: ignore[union-attr]
        verb = "states" if len(unstated) == 1 else "state"
        return Verdict(
            rule="current_budget",
            scope=rail.id,
            status="warn",
            unchecked=True,
            detail=(
                (f"At least {fmt.milliamps(draw)} known load on {rail.id}. " if draw > 0 else f"Load on {rail.id} is unknown. ")
                + f"{supply} is rated {fmt.milliamps(limit)}, but {names} {verb} no draw. "
                "Available headroom cannot be determined."
            ),
            subject=subject,
            involved=involved,
            evidence=evidence,
        )
    headline = f"{fmt.milliamps(draw)} of {fmt.milliamps(limit)} ({fmt.percent(draw / limit) if limit else 'no capacity'})"

    if required > limit:
        return Verdict(
            rule="current_budget",
            scope=rail.id,
            status="fail",
            detail=(
                f"{fmt.milliamps(draw)} on {rail.id} plus {fmt.percent(margin)} margin "
                f"= {fmt.milliamps(required)}, above the {fmt.milliamps(limit)} "
                f"rating of {supply}."
            ),
            subject=subject,
            involved=involved,
            evidence=evidence,
        )

    if draw > DERATING_THRESHOLD * limit:
        headroom = fmt.percent(1 - DERATING_THRESHOLD)
        return Verdict(
            rule="current_budget",
            scope=rail.id,
            status="warn",
            detail=f"{headline} — inside the {headroom} derating band.",
            subject=subject,
            involved=involved,
            evidence=evidence,
        )

    return Verdict(
        rule="current_budget",
        scope=rail.id,
        status="pass",
        detail=headline,
        subject=subject,
        involved=involved,
        evidence=evidence,
    )


# ── R5 · thermal_dissipation ──────────────────────────────────────────────────


def thermal_dissipation(board: Board) -> list[Verdict]:
    """How much heat the part making a rail has to shed, and whether it can.

    linear:    P = (vin − vout) × draw
    switching: P = vout × draw × (1/η − 1)
    ΔT = P × θJA, and θJA is an approximation — see `packages`.

    The junction temperature is checked against the part's own maximum, not against
    `requirements.temp_range`. That range is a component *grade* — commercial 0–70,
    industrial −40–85 — describing the ambient conditions a part must be rated for.
    Comparing a junction temperature to it is a category error: it would fail every
    regulator that runs warm inside a commercial-grade product, which is all of them.
    """
    verdicts: list[Verdict] = []
    for rail in board.rails.values():
        verdict = _check_rail_thermal(board, rail)
        if verdict is not None:
            verdicts.append(verdict)
    return verdicts


def _dissipation(
    board: Board, rail: Rail, regulator: PartSpec, draw: float
) -> tuple[float | None, float | None, str, str, tuple[float, float] | None]:
    """Returns (low watts, high watts, topology label, reason, efficiency band).

    The output voltage is the *rail*, not anything on the regulator's datasheet. A
    regulator is chosen to make the rail it sources; whether it can be set that low is
    R1's business, and reading a range maximum here produced `(5.0 − 32.04) × draw` —
    negative watts, which passes every ceiling.
    """
    if regulator.regulation == "switching":
        band = (regulator.efficiency, regulator.efficiency)
        if regulator.efficiency is None:
            band = efficiency.band_for(regulator)
        if band is None:
            return None, None, regulator.topology or "switching", "has no efficiency band", None
        low_efficiency, high_efficiency = band
        output_power = rail.voltage * draw
        return (
            output_power * (1 / high_efficiency - 1),
            output_power * (1 / low_efficiency - 1),
            regulator.topology or "switching",
            "",
            None if regulator.efficiency is not None else band,
        )

    if regulator.regulation is None:
        return (
            None,
            None,
            "unknown",
            "states no topology and its category does not imply a regulation type",
            None,
        )

    input_rail = board.input_rail(rail.source) if rail.source else None
    if input_rail is None:
        return None, None, "linear", "has no known input rail", None
    power = (input_rail.voltage - rail.voltage) * draw
    return power, power, "linear", "", None


def _power_range(low: float, high: float) -> str:
    """One power value when exact, otherwise the interval it can occupy."""
    if low == high:
        return fmt.watts(low)
    return f"{fmt.watts(low)}–{fmt.watts(high)}"


def _check_rail_thermal(board: Board, rail: Rail) -> Verdict | None:
    if not rail.source:
        return None
    regulator = board.part(rail.source)
    if regulator is None:
        return None

    consumers = rail_current.consumers(board, rail)
    draw, unstated = rail_current.rail_draw(board, consumers)
    if consumers and draw <= 0 and unstated:
        return Verdict("thermal_dissipation", "warn",
            f"The load on {rail.id} is unknown; regulator heating cannot be evaluated.",
            rail.source, involved=(rail.source, *(key for key, _ in consumers)), scope=rail.id, unchecked=True)
    if not consumers or draw <= 0:
        return None

    # Heat computed from a partial draw is a *floor*: the parts that stated nothing can
    # only add to it. A regulator already over its junction limit on the known load is
    # over it, and declining to say so would be the quietest way to miss a dead board.
    partial = bool(unstated)

    subject = rail.source
    involved = (subject, *(s for s, _ in consumers))
    requirements = board.requirements
    evidence = regulator.cite(subject, "package", "temp_max")
    input_rail = board.input_rail(subject)
    if input_rail is not None:
        evidence += _supply_evidence(input_rail, subject)
    power_low, power_high, topology, blocked, band = _dissipation(board, rail, regulator, draw)
    if regulator.is_switching:
        evidence += efficiency_evidence(regulator, subject, band)
    if power_low is None or power_high is None:
        return Verdict(
            rule="thermal_dissipation",
            scope=rail.id,
            status="warn",
            unchecked=True,
            detail=f"{regulator.mpn} {blocked} — dissipation could not be computed.",
            subject=subject,
            involved=involved,
            evidence=evidence,
        )

    theta = regulator.theta_ja or packages.theta_ja(regulator.package)
    if theta is None:
        return Verdict(
            rule="thermal_dissipation",
            scope=rail.id,
            status="warn",
            unchecked=True,
            detail=(
                f"{regulator.mpn} dissipates {_power_range(power_low, power_high)}, but no θJA is known "
                f"for {regulator.package or 'its package'} — temperature rise unchecked."
            ),
            subject=subject,
            involved=involved,
            evidence=evidence,
        )

    # A θJA carried forward from an earlier run is still a datasheet reading, but *this*
    # run never opened that datasheet. Labelling it "(datasheet)" and putting the quoted
    # line in `source` would tell the screen a datasheet was consulted here and print the
    # quote twice. Name the dossier instead, exactly as the package table names itself.
    theta_from_dossier = is_dossier_source(regulator.provenance.get("theta_ja", ""))
    theta_evidence = (
        Evidence(
            subject,
            "θJA (dossier)" if theta_from_dossier else "θJA (datasheet)",
            f'{fmt.ohms_per_watt(theta)} — "{regulator.theta_ja_source_line}"',
            DOSSIER_SOURCE if theta_from_dossier else regulator.datasheet,
        )
        if regulator.theta_ja is not None and regulator.theta_ja_source_line is not None
        else Evidence(subject, "θJA (package table)", fmt.ohms_per_watt(theta), packages.THETA_JA_SOURCE)
    )
    evidence += (
        theta_evidence,
        Evidence(subject, "current basis", "peak, assumed continuous", CURRENT_BASIS_SOURCE),
    )

    if regulator.temp_max is None:
        rise = f"{fmt.celsius(power_high * theta)} {'worst-case ' if band is not None else ''}rise"
        return Verdict(
            rule="thermal_dissipation",
            scope=rail.id,
            status="warn",
            unchecked=True,
            detail=(
                f"{regulator.mpn} dissipates {_power_range(power_low, power_high)} — "
                f"{rise}, but it states no maximum temperature to check that against."
            ),
            subject=subject,
            involved=involved,
            evidence=evidence,
        )

    rise_low = power_low * theta
    rise_high = power_high * theta
    junction_low = requirements.ambient_c + rise_low
    junction_high = requirements.ambient_c + rise_high
    limit = regulator.temp_max
    sum_line = _thermal_sum(board, rail, regulator, draw, topology, band)

    floor = " at least" if partial else ""
    caveat = (
        f" This is a floor — {fmt.plural(len(unstated), 'part')} on the rail state no draw."
        if partial
        else ""
    )

    if junction_high <= limit:
        worst_case = (
            f" at worst case ({fmt.percent(band[0])} efficiency)" if band is not None else ""
        )
        if rise_high > THERMAL_RISE_WARN_C:
            return Verdict(
                rule="thermal_dissipation",
                scope=rail.id,
                status="warn",
                detail=(
                    f"{sum_line} = {fmt.watts(power_high)}{worst_case} — "
                    f"{fmt.celsius(rise_high)} rise runs hot even though "
                    f"{fmt.celsius(junction_high)} clears the {fmt.celsius(limit)} limit.{caveat}"
                ),
                subject=subject,
                involved=involved,
                evidence=evidence,
            )

        return Verdict(
            rule="thermal_dissipation",
            scope=rail.id,
            status="warn" if partial else "pass",
            unchecked=partial,
            detail=(
                f"{sum_line} ={floor} {fmt.watts(power_high)}{worst_case} — "
                f"{fmt.celsius(rise_high)} rise in {regulator.package or 'its package'}, "
                f"{fmt.celsius(junction_high)} junction.{caveat}"
            ),
            subject=subject,
            involved=involved,
            evidence=evidence,
        )

    if junction_low > limit:
        best_case = (
            f" at best case ({fmt.percent(band[1])} efficiency)" if band is not None else ""
        )
        return Verdict(
            rule="thermal_dissipation",
            scope=rail.id,
            status="fail",
            detail=(
                f"{sum_line} ={floor} {fmt.watts(power_low)}{best_case} in "
                f"{regulator.package or 'its package'} — {fmt.celsius(rise_low)} rise, "
                f"{fmt.celsius(junction_low)} junction against a {fmt.celsius(limit)} limit."
                f"{caveat}"
            ),
            subject=subject,
            involved=involved,
            evidence=evidence,
        )

    critical_power = (limit - requirements.ambient_c) / theta
    critical_efficiency = 1 / (critical_power / (rail.voltage * draw) + 1)
    return Verdict(
        rule="thermal_dissipation",
        scope=rail.id,
        status="warn",
        unchecked=True,
        detail=(
            f"{sum_line} spans {_power_range(power_low, power_high)} — passes at or above "
            f"~{fmt.percent(critical_efficiency)} efficiency, fails below it; the datasheet's "
            f"efficiency curve at {fmt.volts(rail.voltage)} and {fmt.milliamps(draw)} would settle it."
            f"{caveat}"
        ),
        subject=subject,
        involved=involved,
        evidence=evidence,
    )


def _thermal_sum(
    board: Board,
    rail: Rail,
    regulator: PartSpec,
    draw: float,
    topology: str,
    band: tuple[float, float] | None,
) -> str:
    """The arithmetic, shown. A judge should be able to check it from the screen."""
    vout = regulator.vout if regulator.vout is not None else rail.voltage
    if regulator.is_switching and regulator.efficiency is not None:
        return (
            f"{fmt.percent(regulator.efficiency)} efficient at "
            f"{fmt.volts(vout)} × {fmt.milliamps(draw)}"
        )
    if regulator.is_switching and band is not None:
        return (
            f"{efficiency.band_label(regulator)} {fmt.percent(band[0])}–{fmt.percent(band[1])} "
            f"efficient at {fmt.volts(vout)} × {fmt.milliamps(draw)}"
        )
    input_rail = board.input_rail(rail.source) if rail.source else None
    vin = input_rail.voltage if input_rail else 0.0
    return f"({fmt.volts(vin)} − {fmt.volts(vout)}) × {fmt.milliamps(draw)}"


# ── R6 · availability ─────────────────────────────────────────────────────────


def availability(board: Board) -> list[Verdict]:
    """Sourcing, not electrical — and the trigger our interviews said actually bites.

    FAIL below the stock floor; WARN on end-of-life status or a long lead time.
    """
    verdicts: list[Verdict] = []
    for slot_id, slot in board.slots.items():
        if slot.part is None:
            continue
        verdicts.append(_check_availability(board, slot_id, slot.part))
    return verdicts


def _check_availability(board: Board, slot_id: str, part: PartSpec) -> Verdict:
    """Cites only the fields that drove the verdict.

    Quoting lifecycle and lead time under a stock failure pads the drawer with rows
    that support nothing — and evidence that does not support the claim it sits under
    trains people to stop reading it.
    """
    requirements = board.requirements

    if part.stock is None:
        return Verdict(
            rule="availability",
            status="warn",
            unchecked=True,
            detail=f"{part.distributor} reports no stock figure for {part.mpn}.",
            subject=slot_id,
            involved=(slot_id,),
            evidence=part.cite(slot_id, "stock"),
        )

    if requirements.min_stock is not None and part.stock < requirements.min_stock:
        late = part.lead_time_days is not None and part.lead_time_days > requirements.max_lead_days
        return Verdict(
            rule="availability",
            status="fail",
            detail=(
                f"{part.mpn}: {fmt.count(part.stock)} in stock at {part.distributor}, "
                f"below the {fmt.count(requirements.min_stock)} minimum."
            ),
            subject=slot_id,
            involved=(slot_id,),
            evidence=part.cite(slot_id, "stock", *(("lead_time_days",) if late else ())),
        )

    concerns: list[str] = []
    cited = ["stock"]
    if part.lifecycle in {"nrnd", "obsolete"}:
        label = "not recommended for new designs" if part.lifecycle == "nrnd" else "obsolete"
        concerns.append(f"marked {label}")
        cited.append("lifecycle")
    if part.lead_time_days is not None and part.lead_time_days > requirements.max_lead_days:
        concerns.append(f"{part.lead_time_days}-day lead time")
        cited.append("lead_time_days")

    stocked = f"{part.mpn}: {fmt.count(part.stock)} in stock at {part.distributor}"
    if concerns:
        return Verdict(
            rule="availability",
            status="warn",
            detail=f"{stocked}, but {fmt.listing(concerns)}.",
            subject=slot_id,
            involved=(slot_id,),
            evidence=part.cite(slot_id, *cited),
        )

    return Verdict(
        rule="availability",
        status="pass",
        detail=f"{stocked}.",
        subject=slot_id,
        involved=(slot_id,),
        evidence=part.cite(slot_id, "stock"),
    )


# ── R7 · temperature_rating ──────────────────────────────────────────────────


def temperature_rating(board: Board) -> list[Verdict]:
    """Every placed part must cover the board's required ambient temperature range."""
    verdicts: list[Verdict] = []
    for slot_id, slot in board.slots.items():
        if slot.part is None:
            continue
        verdicts.append(_check_temperature_rating(board, slot_id, slot.part))
    return verdicts


def _check_temperature_rating(board: Board, slot_id: str, part: PartSpec) -> Verdict:
    """Judge each temperature bound independently, citing only the decisive fields."""
    required_min, required_max = board.requirements.temp_range
    cold_failure = part.temp_min is not None and part.temp_min > required_min
    hot_failure = part.temp_max is not None and part.temp_max < required_max

    if cold_failure or hot_failure:
        failed_ends: list[str] = []
        cited: list[str] = []
        if part.temp_min is not None and part.temp_min > required_min:
            failed_ends.append(
                f"the cold end by {fmt.celsius(part.temp_min - required_min)} "
                f"({fmt.celsius(part.temp_min)} versus {fmt.celsius(required_min)})"
            )
            cited.append("temp_min")
        if part.temp_max is not None and part.temp_max < required_max:
            failed_ends.append(
                f"the hot end by {fmt.celsius(required_max - part.temp_max)} "
                f"({fmt.celsius(part.temp_max)} versus {fmt.celsius(required_max)})"
            )
            cited.append("temp_max")
        return Verdict(
            rule="temperature_rating",
            status="fail",
            detail=f"{part.mpn} misses {fmt.listing(failed_ends)}.",
            subject=slot_id,
            involved=(slot_id,),
            evidence=part.cite(slot_id, *cited),
        )

    missing = [
        name
        for name, value in (("temp_min", part.temp_min), ("temp_max", part.temp_max))
        if value is None
    ]
    if missing:
        return Verdict(
            rule="temperature_rating",
            status="warn",
            unchecked=True,
            detail=(
                f"{part.mpn} states no {fmt.listing(missing)} — its temperature grade "
                "could not be checked."
            ),
            subject=slot_id,
            involved=(slot_id,),
            evidence=part.cite(slot_id, "temp_min", "temp_max"),
        )

    return Verdict(
        rule="temperature_rating",
        status="pass",
        detail=(
            f"{part.mpn} is rated {fmt.celsius(part.temp_min)}–{fmt.celsius(part.temp_max)}, "
            f"covering {fmt.celsius(required_min)}–{fmt.celsius(required_max)}."
        ),
        subject=slot_id,
        involved=(slot_id,),
        evidence=part.cite(slot_id, "temp_min", "temp_max"),
    )


# ── footprint · warning only ──────────────────────────────────────────────────


def footprint(board: Board) -> list[Verdict]:
    """Advisory size check. Never fails a board — it only ever raises a flag.

    Silent unless the prompt actually asked for a size limit, since a warning that
    fires on every part is a warning nobody reads.
    """
    ceiling = board.requirements.max_package_mm
    if ceiling is None:
        return []

    verdicts: list[Verdict] = []
    for slot_id, slot in board.slots.items():
        part = slot.part
        if part is None:
            continue
        side = packages.longest_side_mm(part.package)
        if side is None or side <= ceiling:
            continue
        verdicts.append(
            Verdict(
                rule="footprint",
                status="warn",
                detail=(
                    f"{part.mpn} in {part.package} is {fmt.num(side, 1)} mm on its longest "
                    f"side, over the {fmt.num(ceiling, 1)} mm target."
                ),
                subject=slot_id,
                involved=(slot_id,),
                evidence=part.cite(slot_id, "package"),
            )
        )
    return verdicts


def rail_coverage(board: Board) -> list[Verdict]:
    """Report a placed part that no rail mentions, and say which checks it therefore missed.

    Three of the eight rules find their operands through a rail — `voltage_overlap`,
    `current_budget` and `thermal_dissipation` all iterate `board.rails`. A part on no rail
    is invisible to all three, and *silently* so: it appeared on the BOM with every check it
    did receive passing, and nothing said that the ones that matter most for a power part
    had never run.

    That silence is the fault this rule fixes. It cannot fix the coverage — checking a solar
    charge controller needs the panel's voltage, which needs a second externally-sourced rail
    that the model does not have. What it can do is refuse to let an unchecked part look
    like a checked one.

    **Always a warning, never a failure.** Nothing is wrong with the board; something is
    missing from what we know about it. A `fail` here would start a repair loop against a
    part that no rule has found any fault with, and there would be nothing for the loop to
    make better.
    """
    verdicts: list[Verdict] = []
    for slot_id in board.unmodelled_slots():
        part = board.part(slot_id)
        if part is None:
            continue
        verdicts.append(
            Verdict(
                rule="rail_coverage",
                status="warn",
                unchecked=True,
                detail=(
                    f"{part.mpn} is on no modelled power rail, so voltage, current budget "
                    f"and thermal dissipation could not be checked for it. Every other rule "
                    f"ran as normal."
                ),
                subject=slot_id,
                involved=(slot_id,),
                evidence=part.cite(slot_id, "vmin", "vmax"),
            )
        )
    return verdicts


# ── the suite ─────────────────────────────────────────────────────────────────

def energy_budget(board: Board) -> list[Verdict]:
    """How long the board runs on its supply, against how long the brief said it must.

    Every other rule measures an instant: volts now, amps now, degrees now. A brief that
    says *"must last a year"* is asking about charge, and nothing measured charge — so the
    requirement reached no rule, produced no verdict, and was not even reported unchecked.
    Measured 13 Aug on `e-paper badge on a coin cell with BLE, must last a year`, which
    returned **zero conflicts** over a board carrying an ESP8266 module drawing ~70 mA on
    transmit. The brief's headline requirement was the one thing nobody looked at.

    **What this rule will not do is guess a duty cycle.** Runtime is capacity divided by
    *average* draw, and average draw depends on how often the radio wakes — which no
    datasheet states and no distributor publishes. Inventing a plausible-looking duty
    cycle would produce a confident number resting on a figure this system made up.

    So it computes the one runtime that needs no assumption: **continuous draw**, every
    part awake at once. That is a genuine lower bound on life, and it decides one case
    honestly in each direction.

    - Lasts long enough even flat out → `pass`. Nothing about the duty cycle can make it
      worse than the bound, so the requirement is met outright.
    - Does not → `warn`, carrying both figures. It is not a `fail`, because duty cycling
      is exactly how such boards are built and the gap may well be closable. It is not
      silence either, which is what it used to be.

    A missing capacity or an unstated draw is reported as unchecked, naming what is
    missing, rather than defaulting to a number that would make the board look measured.
    """
    required = board.requirements.lifetime_hours
    if required is None:
        return []

    capacity = board.requirements.supply_capacity_mah
    if capacity is None:
        return [
            Verdict(
                rule="energy_budget",
                status="warn",
                unchecked=True,
                detail=(
                    f"This board is asked to run for {fmt.duration(required)}, but its supply "
                    f"states no capacity, so how long it lasts cannot be checked."
                ),
                subject=_energy_subject(board),
                scope="board",
            )
        ]

    # Summed on the rails the *supply* feeds, not over every slot. `rail_draw` reflects a
    # regulator's output back through the conversion, so a load is counted once rather
    # than twice — once on its own rail and again inside the regulator above it.
    total = 0.0
    unstated: list[str] = []
    for rail in board.rails.values():
        if rail.source is not None:
            continue
        amps, missing = rail_current.rail_draw(board, rail_current.consumers(board, rail))
        total += amps
        unstated += [f"{s} ({p.mpn})" for s in missing if (p := board.part(s)) is not None]

    if unstated:
        return [
            Verdict(
                rule="energy_budget",
                status="warn",
                unchecked=True,
                detail=(
                    f"This board is asked to run for {fmt.duration(required)} on "
                    f"{capacity:g} mAh, but {fmt.listing(unstated)} "
                    f"{'state' if len(unstated) > 1 else 'states'} no current draw, so the "
                    f"runtime cannot be computed."
                ),
                subject=_energy_subject(board),
                scope="board",
            )
        ]

    if total <= 0:
        return []

    hours = capacity / (total * 1000.0)
    met = hours >= required
    return [
        Verdict(
            rule="energy_budget",
            status="pass" if met else "warn",
            detail=(
                f"{capacity:g} mAh at {total * 1000:.1f} mA continuous is "
                f"{fmt.duration(hours)}, against the {fmt.duration(required)} asked for"
                + (
                    "."
                    if met
                    else " — reaching it needs duty cycling, which is not modelled here."
                )
            ),
            subject=_energy_subject(board),
            scope="board",
        )
    ]


def _energy_subject(board: Board) -> str:
    """The heaviest placed part, so the finding lands on something a person can act on."""
    placed = [(s, p) for s, slot in board.slots.items() if (p := slot.part) is not None]
    if not placed:
        return next(iter(board.slots), "board")
    return max(placed, key=lambda pair: pair[1].draw or 0.0)[0]


RULES = (
    voltage_overlap,
    interface_role_match,
    pin_budget,
    current_budget,
    thermal_dissipation,
    availability,
    footprint,
    temperature_rating,
    energy_budget,
    # Last: it reports on the *absence* of the checks above rather than on the board.
    rail_coverage,
)


def evaluate(board: Board) -> list[Verdict]:
    """Run every rule. Order is stable, so the event stream is reproducible."""
    from .integrity import prepare
    checked, issues = prepare(board)
    if any(v.scope == "requirements" for v in issues):
        return issues + [Verdict(rule.__name__, "warn", "Invalid operating requirements must be corrected before this check can run.",
            next(iter(board.slots), "board"), unchecked=True) for rule in RULES]
    verdicts = [verdict for rule in RULES for verdict in rule(checked)]
    uncertain_power = any(v.rule == "power_topology" for v in issues)
    for index, verdict in enumerate(verdicts):
        if verdict.status != "pass":
            continue
        rail = checked.rails.get(verdict.scope)
        reason = None
        if rail and rail.basis and rail.basis.startswith("Inferred from BOM"):
            reason = "The inferred power connection has not been confirmed."
        elif uncertain_power and verdict.rule in {"voltage_overlap", "current_budget", "thermal_dissipation", "energy_budget"}:
            reason = "Power topology is incomplete or ambiguous; review the connection findings."
        elif verdict.rule in {"current_budget", "thermal_dissipation", "energy_budget"}:
            dependencies = rail_current.dependencies(checked, verdict.involved)
            loads = [checked.part(key) for key in dependencies]
            if any(part and part.i_peak is None and part.i_typ is not None for part in loads):
                reason = "Only typical current is known for some loads; worst-case operation is unverified."
            elif any(not checked.placed(key) for key in dependencies):
                reason = "Some connected loads have not been resolved; total demand is unknown."
            elif verdict.rule == "energy_budget" and any(part and part.is_switching and part.efficiency is None for part in loads):
                reason = "Battery runtime depends on converter efficiency that has not been verified."
        if reason:
            verdicts[index] = replace(verdict, status="warn", unchecked=True, detail=verdict.detail + " " + reason)
    return issues + verdicts


def failures(verdicts: list[Verdict]) -> list[Verdict]:
    return [v for v in verdicts if v.status == "fail"]


def passing(verdicts: list[Verdict]) -> list[Verdict]:
    return [v for v in verdicts if v.status == "pass"]


def for_subject(verdicts: list[Verdict], slot_id: str) -> list[Verdict]:
    """The checks attributed to one slot — what a `check` event stream for it contains."""
    return [v for v in verdicts if v.subject == slot_id]
