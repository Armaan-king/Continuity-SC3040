"""Core value types for the constraint engine.

Mirrors `docs/specs/2026-08-02-contract.md` §3. Every type here is frozen: the engine
never mutates a part, a slot or a verdict — graph nodes return new objects.

Nothing in this module imports a network client or an LLM. That is deliberate. The
engine decides what is broken; it does so from fetched spec fields alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, Mapping

from . import regulation as regulation_facts
from .format import listing

# ── enums, as the contract spells them ────────────────────────────────────────

Tier = Literal["core", "power", "peripherals", "passives"]
"""Render order left-to-right is POWER · CORE · PERIPHERALS · PASSIVES, not this order."""

CheckStatus = Literal["pass", "warn", "fail"]
SlotStatus = Literal["pending", "searching", "pass", "conflict"]
EdgeKind = Literal["power", "data"]
EdgeStatus = Literal["pending", "pass", "conflict", "unchecked"]
"""`unchecked` is drawn-but-never-evaluated — see `topology.unmodelled`. It was added to
the wire and to the frontend on 13 Aug and missed here, so every stub edge carried a status
this type said could not exist. Nothing failed: `Literal` is not enforced at runtime, which
is exactly why it is worth keeping honest."""
Role = Literal["master", "peripheral", "passive"]
Lifecycle = Literal["active", "nrnd", "obsolete", "unknown"]
Priority = Literal["cost", "size", "availability"]
RepairAction = Literal[
    "swap", "change_topology", "add_part", "change_rail", "relax_requirement", "escalate"
]

RULE_NAMES = (
    "voltage_overlap",
    "interface_role_match",
    "pin_budget",
    "current_budget",
    "thermal_dissipation",
    "availability",
    "footprint",
    "temperature_rating",
    "energy_budget",
    "rail_coverage",
)


# ── evidence ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Evidence:
    """One row of proof behind a verdict.

    `field` and `value` are **verbatim** from the distributor payload — never
    synthesised, never reformatted. They are the honesty mechanism: a judge can read
    the value on screen and open `source` to check it.

    `source` is normally a datasheet URL. Where a figure comes from our own package
    table rather than the part's datasheet, `source` names that table instead, so the
    screen never implies a datasheet said something it did not.
    """

    slot: str
    field: str
    value: str
    source: str | None = None


@dataclass(frozen=True)
class Verdict:
    """The result of one rule against one subject. Returned by every rule.

    `subject` is the slot the check is attributed to — it drives `check.slot` on the
    wire, and on failure it is the slot most likely at fault.

    `involved` is every slot *participating* in the constraint. Three parts sharing a
    rail are all involved in its current budget; only one of them is the regulator.
    Conflating the two produced a real bug in the frontend, so keep them apart:
    `involved` feeds the legal set, `subject` feeds attribution.
    """

    rule: str
    status: CheckStatus
    detail: str
    subject: str
    involved: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()

    scope: str | None = None
    """The net this verdict is about, where one applies — a rail id like "3V3".

    A two-rail board produces two `current_budget` results, and on the demo board both
    land on the regulator: it draws from 5V0 and it feeds 3V3. Without a scope those
    are one check overwriting the other. Checks are keyed by (rule, subject, scope).
    """

    unchecked: bool = False
    """True when a figure the rule needs was not published, so it could not decide.

    Always paired with `warn`, never with `fail`. `warn` alone also covers genuine
    concerns — a rail inside its derating band, a part near end of life — and the two
    must not be told apart by wording. DS-16 analytics counts unchecked evaluations in a
    column of their own and withholds a rate when they dominate it; a status that had
    to be parsed out of `detail` would drift the first time a sentence was reworded.
    """

    original_status: CheckStatus | None = None
    accepted: bool = False

    def __post_init__(self) -> None:
        # Deduplicate, subject first. A rail whose only consumer *is* its subject —
        # the board input rail feeding one regulator — otherwise lists that slot twice,
        # and the conflict drawer renders it as two affected components.
        ordered = dict.fromkeys((self.subject, *self.involved))
        object.__setattr__(self, "involved", tuple(ordered))
        if self.unchecked and self.status != "warn":
            raise ValueError(f"{self.rule}: unchecked is only meaningful with warn, not {self.status}")

    @property
    def failed(self) -> bool:
        return self.status == "fail"


# ── parts ─────────────────────────────────────────────────────────────────────


ASSUMED_EFFICIENCY = 0.80
"""Conversion efficiency used when a switching regulator publishes none.

Measured across six live boards: **0 of 33 parts** stated one, because JLCPCB does not
carry the parameter. Returning *unchecked* for every switcher meant R5 never evaluated
one — and since `change_topology: buck` is the usual repair for a thermal or current
conflict, the engine's standard fix moved parts into the one place it could not check.

0.80 is deliberately below what a real buck achieves (85–95%), so both the heat and the
reflected input current come out overstated. Erring the other way passes boards that
cook. Stated exactly like θJA and an inferred supply: assumed, and said out loud.
"""

ASSUMED_EFFICIENCY_SOURCE = "Continuity assumption — no efficiency published"

DOSSIER_SOURCE = "Continuity dossier — learned in an earlier run"
"""Prefix marking a field carried forward from a past run rather than this run's listing.

It lives here, beside the other source strings, because both the engine and `parts` have
to recognise it and `engine` may not import `parts`. Matching it as a literal in two
modules is how it first arrived; a reworded string would then have silently stopped being
recognised while every test still passed.
"""


@dataclass(frozen=True)
class PartSpec:
    """A normalised part. Produced by the `normalize` node, cached by MPN.

    `None` on an electrical field means *the distributor did not state it* — it never
    means zero. Rules that cannot evaluate a null field say so out loud rather than
    skipping quietly.
    """

    mpn: str
    manufacturer: str
    description: str
    category: str

    # electrical
    vmin: float | None = None
    vmax: float | None = None
    vout_min: float | None = None
    vout_max: float | None = None
    """The output range a regulator can be set to. Equal ends mean a fixed part.

    Stored as a range because most adjustable regulators publish one — the TPS5430 is
    1.221 V–32 V, the LM2596-ADJ is 1.23 V–37 V — and collapsing that to a single figure
    took the *maximum* as though it were an output. A board whose highest rail was 5 V
    then had a repair proposed against "the required output is 32.04 V".
    """
    i_typ: float | None = None
    i_peak: float | None = None
    i_max: float | None = None
    interfaces: tuple[str, ...] = ()
    role: Role | None = None
    pins_required: int | None = None
    pins_available: int | None = None
    package: str | None = None
    theta_ja: float | None = None
    theta_ja_source_line: str | None = None
    topology: str | None = None
    synchronous: bool | None = None
    efficiency: float | None = None
    temp_min: float | None = None
    temp_max: float | None = None

    # commercial
    unit_price: float | None = None
    currency: str = "USD"
    stock: int | None = None
    distributor: str = "unknown"
    lifecycle: Lifecycle = "unknown"
    lead_time_days: int | None = None
    datasheet: str | None = None
    product_url: str | None = None

    # provenance
    raw: Mapping[str, str] = field(default_factory=dict)
    """Verbatim distributor parameters, exactly as fetched."""

    provenance: Mapping[str, str] = field(default_factory=dict)
    """Normalised field name → the `raw` key it came from.

    Filled by the normalise step alongside the values themselves. Without it a rule
    would have to guess which distributor parameter backs `vmin`, and the evidence
    rows would be guesses too.
    """

    fact_evidence: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    research: tuple[Mapping[str, object], ...] = ()

    @property
    def draw(self) -> float | None:
        """Worst-case current this part pulls: peak, falling back to typical."""
        return self.i_peak if self.i_peak is not None else self.i_typ

    @property
    def vout(self) -> float | None:
        """The voltage this part actually produces, or None if that is not a single figure.

        An adjustable regulator produces nothing in particular until its feedback divider
        is chosen, so `None` is the honest answer — and it is what the contract's
        `vout: number | null` was always meant to carry. Callers that need the working
        output voltage should read the *rail*, which is what the regulator was picked to
        make; `produces` below is what checks the two against each other.
        """
        if self.vout_min is None or self.vout_max is None:
            return None
        return self.vout_min if self.vout_min == self.vout_max else None

    def produces(self, voltage: float) -> bool | None:
        """Can this part be set to `voltage`? None when that cannot be established.

        Each bound is judged on its own, the same discipline `_check_one_supply` applies
        to `vmin`/`vmax`, and for the same reason: output data is one-sided far more
        often than complete. An adjustable part frequently publishes only the top of its
        range, and demanding both ends before deciding anything would report *unchecked*
        on a fixed 5 V regulator sitting on a 3V3 rail — a definite failure, silently
        downgraded to a shrug.
        """
        if self.vout_max is not None and voltage > self.vout_max:
            return False
        if self.vout_min is not None and voltage < self.vout_min:
            return False
        if self.vout_min is None or self.vout_max is None:
            return None
        return True

    @property
    def conversion_efficiency(self) -> float:
        """Published efficiency, or the assumption. Only meaningful for a switcher."""
        return self.efficiency if self.efficiency is not None else ASSUMED_EFFICIENCY

    @property
    def regulation(self) -> Literal["switching", "linear"] | None:
        """Known regulator behaviour from a topology first, then its category.

        A topology or category that does not map to an engine-owned fact remains
        unknown. It is not safe to infer a linear regulator from missing data.
        """
        return regulation_facts.regulation_from_topology(
            self.topology
        ) or regulation_facts.regulation_from_category(self.category)

    @property
    def is_switching(self) -> bool:
        return self.regulation == "switching"

    def cite(self, slot: str, *normalised_fields: str) -> tuple[Evidence, ...]:
        """Evidence rows for the named normalised fields, verbatim where possible.

        Degrades honestly: if the normalise step recorded no provenance for a field,
        the row is labelled as derived rather than being passed off as a quoted
        distributor parameter.
        """
        rows: list[Evidence] = []
        seen: set[str] = set()
        for name in normalised_fields:
            raw_key = self.provenance.get(name)
            if raw_key is not None and raw_key in self.raw:
                # vmin and vmax routinely share one parameter ("2.5V ~ 6.0V"); quoting it
                # twice makes the drawer look padded rather than thorough.
                if raw_key in seen:
                    continue
                seen.add(raw_key)
                source = self.fact_evidence.get(name, {}).get("source")
                rows.append(Evidence(slot, raw_key, self.raw[raw_key], source if isinstance(source, str) else self.datasheet))
                continue
            value = getattr(self, name, None)
            if value is None:
                continue
            if raw_key is not None and raw_key.startswith(DOSSIER_SOURCE):
                display_value = listing(value) if isinstance(value, tuple) else str(value)
                rows.append(Evidence(slot, f"{name} (dossier)", display_value, raw_key))
                continue
            label = f"{name} (derived)"
            if label in seen:
                continue
            seen.add(label)
            display_value = listing(value) if isinstance(value, tuple) else str(value)
            rows.append(Evidence(slot, label, display_value, self.datasheet))
        return tuple(rows)


# ── requirements ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Requirements:
    """Extracted from the prompt by `parse_requirements`.

    Fixed schema — the LLM fills declared fields only and can never add one. Unfilled
    fields keep the defaults below.
    """

    temp_range: tuple[int, int] = (0, 70)
    """"industrial" → (-40, 85)."""

    current_margin: float = 0.15
    """Design headroom above worst-case draw. "battery"/"low power" → 0.30.

    Applied on top of a sum of every part's *peak*, which already assumes all peaks
    coincide. 15% over an already worst-case figure is the conventional headroom; the
    30% this once defaulted to was double-conservative and rejected real designs.
    """

    max_package_mm: float | None = None
    input_source: str = "usb-5v"
    input_voltage: float | None = None
    """A supply voltage the brief stated outright, in volts.

    `input_source` is a *classification* into a fixed vocabulary, and a vocabulary that
    cannot represent the user's supply does not refuse — it rounds. "48V industrial bus"
    became `24v-industrial` and "two 18650 cells in series" became `battery-3v7`, both
    exactly half, both silent, both producing boards that looked clean.

    A voltage the user wrote down is sourced data, so it wins. The classification is
    still used for the current limit, which the brief usually does not state.
    """
    priority: Priority = "availability"
    ambient_c: int = 25
    min_stock: int | None = 100
    """The minimum distributor stock a part must have, when the user set one."""
    max_lead_days: int = 30

    lifetime_hours: float | None = None
    """How long the brief says the board must run on its supply. "a year" → 8760.

    `None` means the brief did not ask, and R10 then has nothing to check. It is the
    asking that matters: a stated lifetime used to reach no rule at all, so a coin-cell
    board carrying a WiFi module reported zero conflicts against a requirement nothing
    had looked at.
    """

    supply_capacity_mah: float | None = None
    """Charge available from the input supply, carried here by the planner.

    The rules import nothing, so the supply vocabulary cannot be read from `engine`. The
    planner resolves the classification and passes the figure down, the same way
    `input_voltage` arrives already decided.
    """


# ── board topology ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Slot:
    id: str
    label: str
    tier: Tier
    pinned: bool = False
    """The user named it in the prompt. Never swapped."""

    status: SlotStatus = "pending"
    part: PartSpec | None = None
    constraint: Mapping[str, object] | None = None
    """Hard filter carried over from the last repair, pushed down into the next search."""

    repair_count: int = 0
    """Escalates above MAX_REPAIRS. See `policy`."""

    def with_part(self, part: PartSpec, status: SlotStatus = "pass") -> "Slot":
        return replace(self, part=part, status=status)


@dataclass(frozen=True)
class Edge:
    id: str
    source: str
    target: str
    label: str | None = None
    """"3V3", "I2C" — null until the chosen parts resolve it."""

    kind: EdgeKind = "data"
    status: EdgeStatus = "pending"


@dataclass(frozen=True)
class Rail:
    """A power net. Rails are how the electrical rules find each other's operands."""

    id: str
    voltage: float
    source: str | None = None
    """Slot producing this rail. None means an external supply (USB, battery)."""

    members: tuple[str, ...] = ()
    """Slots drawing from it. The producing slot is *not* a member of its own output."""

    i_limit: float | None = None
    """Current ceiling for an externally-sourced rail, e.g. USB-C 5 V at 3 A."""

    basis: str | None = None
    """Where this rail's own numbers came from, when they were not derived from a part.

    A rail fed by a regulator takes its voltage from that part's datasheet, and the
    verdict already cites it. The board's *input* rail has no part behind it — its
    numbers come either from a published standard ("USB Type-C default Rp
    advertisement") or from reading the user's brief, and those two are not equally
    trustworthy.

    Every rule that treats a rail's own voltage or current limit as an operand cites
    this, so a verdict computed against a guessed 6 V says so rather than presenting it
    with the same confidence as a spec figure. Same mechanism as `PartSpec.provenance`
    and the θJA row: the number travels with its source.
    """


def slots_without_a_rail(
    slots: Mapping[str, Slot], rails: Mapping[str, Rail]
) -> tuple[str, ...]:
    """Slots the power tree never mentions — neither a member of a rail nor the source of one.

    On a solar board that is the charge controller and the battery holder: the plan declares
    the rail the buck *makes*, and nothing about what charges the cell that feeds it.

    One definition, used twice and for two different purposes — the rule that reports these
    parts as unchecked, and the renderer that draws them so they do not float. Those two must
    agree about which parts they are talking about, and the only way to guarantee that is for
    there to be one predicate.
    """
    known = {member for rail in rails.values() for member in rail.members}
    known |= {rail.source for rail in rails.values() if rail.source}
    return tuple(slot_id for slot_id in slots if slot_id not in known)


@dataclass(frozen=True)
class Port:
    component: str
    name: str
    role: Literal["controller", "peripheral"]


@dataclass(frozen=True)
class SignalNet:
    id: str
    protocol: str
    ports: tuple[Port, ...]
    basis: str = "inferred"


@dataclass(frozen=True)
class Board:
    """Everything the rules need, and nothing they do not."""

    requirements: Requirements
    slots: Mapping[str, Slot]
    rails: Mapping[str, Rail]
    signal_nets: tuple[SignalNet, ...] = ()

    def part(self, slot_id: str) -> PartSpec | None:
        slot = self.slots.get(slot_id)
        return slot.part if slot else None

    def placed(self, slot_id: str) -> bool:
        return self.part(slot_id) is not None

    def input_rail(self, slot_id: str) -> Rail | None:
        """The rail feeding `slot_id` — the one it is a member of, not the one it makes."""
        for rail in self.rails.values():
            if slot_id in rail.members:
                return rail
        return None

    def sources_a_rail(self, slot_id: str) -> bool:
        """Whether this slot makes a rail — i.e. whether it is a regulator on this board."""
        return any(rail.source == slot_id for rail in self.rails.values())

    def unmodelled_slots(self) -> tuple[str, ...]:
        """Slots on no rail at all. See `slots_without_a_rail`."""
        return slots_without_a_rail(self.slots, self.rails)


# ── repair ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Alternative:
    mpn: str
    manufacturer: str
    reason: str
    recommended: bool = False
    unit_price: float | None = None
    currency: str = "USD"
    stock: int | None = None
    lead_time_days: int | None = None
    datasheet: str | None = None


@dataclass(frozen=True)
class Repair:
    slot: str
    action: RepairAction
    rationale: str
    constraint: Mapping[str, object] = field(default_factory=dict)
    alternatives: tuple[Alternative, ...] = ()
