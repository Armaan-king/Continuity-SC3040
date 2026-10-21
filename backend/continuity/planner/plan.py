"""Prompt → a board the engine can check.

The planner decides *what is on the table*: which slots exist, what rail each hangs
off, what the board is plugged into, and what to search for. It never decides whether
anything is compatible — that is the engine's job, and keeping the two apart is why
`engine/` imports nothing from this package.

## The same fence as everywhere else

The model **classifies into vocabularies the engine owns** and never supplies an
operand. It picks `"usb-5v"`, not `5.0` volts. It picks a tier from four names. Every
field it returns is validated against a declared schema, and a rail referring to a slot
that does not exist is dropped rather than merged — the same rule the normaliser runs
on, applied to a different payload.

## Queries are short and part-shaped

Learned from the live API rather than assumed. JLCPCB's search runs a parser over the
query, pins a subcategory from what it detects, and matches the remaining words *inside*
it — so every extra word narrows rather than widens. `"ESP32 module"` finds the radio
that `"ESP32-S3 wifi bluetooth module"` does not, because "wifi" appears nowhere in the
indexed record. The prompt below says so explicitly, because it is the single easiest
way for a planner to return a board full of empty slots.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from .. import llm
from ..engine.models import Rail, Requirements, Slot, Tier
from ..parts import categories
from .topology import BATTERY_SOURCES, INPUT_SOURCES, capacity_of

log = logging.getLogger(__name__)

TIERS: tuple[Tier, ...] = ("power", "core", "peripherals", "passives")
PRIORITIES = ("cost", "size", "availability")
UNRESOLVED = "unresolved"
"""Input source the planner could not place in the vocabulary. Routes to `clarify`."""

MAX_SLOTS = 10
"""A board the graph can actually get through, and a screen it can fit on."""

SLOT_ID = re.compile(r"^[a-z][a-z0-9_]{0,30}$")


@dataclass(frozen=True)
class Plan:
    requirements: Requirements
    slots: dict[str, Slot]
    rails: list[Rail]
    queries: dict[str, str]
    links: tuple[tuple[str, str], ...]
    """Intended master → peripheral bus links. The bus itself is unknown until parts land."""

    order: tuple[str, ...]
    """Placement order: power first, then the controller, then what hangs off it."""


SYSTEM = f"""You plan printed circuit boards. Given a description, return ONE JSON object
describing the parts the board needs. You do not decide whether parts are compatible —
another system does that.

Completeness is part of your job:
  Every bus peripheral needs a controller on the board. If the brief names a display,
  sensor or memory and does not name a microcontroller, add one. A peripheral with
  nothing to drive it is not a board.
    GOOD: "industrial sensor with an OLED" → sensor, OLED, microcontroller
    BAD:  "industrial sensor with an OLED" → sensor, OLED
  Every functional block the user names gets a slot, however implausible. If the brief
  says a 500 W amplifier on a wearable, plan the 500 W amplifier. You do not decide
  whether the design is sensible — another system does that, and it can only report a
  contradiction it can see. Dropping a part because it looks wrong hides the fault
  instead of surfacing it.

Return exactly these keys: input_source, input_voltage, temp_range, current_margin,
priority, min_stock, ambient_c, lifetime_hours, slots, rails, links.

slots: 2 to {MAX_SLOTS} objects, each with:
  id       lowercase snake_case, unique, e.g. "regulator", "mcu", "imu"
  label    2-4 words for a UI, e.g. "IMU Sensor"
  tier     one of: power, core, peripherals, passives
  pinned   true if the user named this function, false if it is an implementation
           detail they did not ask for (regulators, level shifters, passives)
  query    SHORT distributor search text — 2 to 4 words, part-shaped
  category what KIND of part this is. Required on EVERY slot. One of:
{categories.prompt_lines()}
           This is filtered on properly, so it costs nothing to be right and it is the
           only thing standing between a sensor slot and a fuse clip: searching for
           "environmental sensor" really did return "5x20 Environmentally Friendly Fuse
           Clip", because the words matched RoHS marketing copy. Do NOT put the category
           in `query` — the text search cannot tell these apart.
  topology for a regulator slot ONLY: "ldo", "buck", "boost" or "buck-boost". Say which
           when the power tree decides it — a rail ABOVE its input needs "boost", a rail
           below it can be "buck" or "ldo". Omit for anything that is not a regulator.
           Do NOT put the topology in `query`; the distributor's text search ignores it
           and this field is filtered on properly.

CRITICAL — how to write `query`:
  The distributor's search detects a component type from your words, restricts to that
  category, then matches the REST of your words inside it. Extra words REDUCE results.
    GOOD: "ESP32 module"  "3.3V LDO regulator"  "OLED display"  "IMU accelerometer"
    BAD:  "ESP32-S3 wifi bluetooth module"   (returns nothing: "wifi" is not indexed)
    BAD:  "small OLED display module I2C"    (returns nothing)
  Never put interfaces, features or adjectives in the query.
  Name the PART, never the application it serves. The index knows what a part measures,
  not what it is for.
    GOOD: "temperature humidity sensor"      BAD: "environmental sensor"
    GOOD: "capacitive moisture sensor"       BAD: "soil sensor"
  Both BAD lines return zero results, and "environmental sensor" then degrades into
  "5x20 Environmentally Friendly Fuse Clip" — the words matched RoHS boilerplate.

rails: the power tree. Each rail has:
  id       e.g. "3V3", "5V", "1V8"
  voltage  a number in volts
  source   the slot id of the regulator producing it
  members  slot ids powered by it (NOT the source itself)
  Do NOT include the board's input supply as a rail; that is added separately.

  A CHARGER counts as a source. If the board has an on-board battery charged by a charge
  controller, declare the battery's rail with the charger as its `source` and everything
  the battery powers as its `members` — the regulator, and the battery holder itself. Its
  `voltage` is the charger's output, i.e. 4.2 for a single Li-ion cell, 3.6 for LiFePO4.
  The board's input is then whatever feeds the CHARGER: the solar panel, or the USB port.
  Without this the charge controller sits on no rail at all and nothing checks it.

  For EVERY rail, compare its voltage against what feeds it before choosing the source
  slot's `topology`. What feeds the first rail is the board input — `input_voltage` if
  you set one, otherwise the voltage of the `input_source` you named:
      2x AA is 3.0 V, a coin cell is 3.0 V, Li-ion is 3.7 V, USB is 5.0 V
  Then:
      rail voltage BELOW the input   → "ldo" or "buck"
      rail voltage ABOVE the input   → "boost". Nothing else can do it.
  A 3V3 rail on 2x AA cells is 3.3 V from 3.0 V, so that regulator is a BOOST. Getting
  this wrong does not break the board — a later stage will catch it — but it wastes a
  whole search, so do the subtraction now.

links: array of [master_slot_id, peripheral_slot_id] pairs for data connections.

input_source: choose the closest of: {", ".join(sorted(INPUT_SOURCES))}
  If none of them fits what the user described, use "{UNRESOLVED}". Never invent
  voltages or currents — pick a name from this list or say unresolved.

input_voltage: the supply voltage in volts, IF the brief states or implies one.
  Read it out of the words, including arithmetic the brief states: "two 18650 cells in
  series" is 7.4, "48V industrial bus" is 48, "3S LiPo" is 11.1. Use null when the brief
  names no voltage at all ("USB powered", "battery powered", "solar"). Never guess a
  typical voltage for a named supply you were not given a figure for — input_source
  already covers that case.

temp_range: [min, max] in Celsius. Commercial [0,70]; industrial [-40,85].
current_margin: 0.15 normally, 0.30 for battery or low-power designs.
priority: one of {PRIORITIES}.
min_stock: units they must be able to buy. 100 unless a production volume is stated.
ambient_c: expected ambient temperature, 25 unless stated.
lifetime_hours: how long the board must run on its own supply, in hours, ONLY when the
  brief asks for it — "must last a year" is 8760, "a month between charges" is 730,
  "runs for a week" is 168. Omit it entirely when no lifetime is stated. Do not infer one
  from the word "battery": a battery board with no stated lifetime has no requirement.

Return the JSON object and nothing else."""


# ── validation ────────────────────────────────────────────────────────────────


"""Supplies where the board runs *off* a cell, so headroom is a lifetime question.

Imported from `topology` rather than restated here: `power_source` uses the same set to
decide whether a stated pack voltage may keep its cell chemistry's current ceiling. Two
copies of one list is two things that must agree, and the one that drifts loses either a
design margin or a current budget silently.

`usb-5v+liion` is deliberately absent from it. A board with a battery *backup* is
mains-powered in normal use, and the wider margin there rejects designs that are fine —
the same double-conservatism that moved the default from 0.30 to 0.15 in the first place.
"""
BATTERY_MARGIN = 0.30
"""Current headroom a battery-fed board gets whatever the planner asked for.

The prompt has always said "0.30 for battery or low-power designs" and the BLE beacon
run on 8 Aug came back with 0.15 regardless. It complies now, but compliance is not a
rule — nothing prevented it, and a design margin that depends on a model's mood is not
a margin. `input_source` already states whether this is a battery, so the engine derives
the floor rather than asking for it.

A floor, not a setting: a planner asking for *more* headroom keeps it.
"""

PLANNABLE_TOPOLOGIES = {"ldo", "buck", "boost", "buck-boost"}


def _slot_constraint(entry: Mapping[str, Any]) -> dict[str, str] | None:
    """What the planner said this slot *is*, carried into the search rather than the label.

    A slot planned as a "Boost Converter" used to be searched for by that phrase alone,
    and JLCPCB's text search does not weight the word — the first live candidate for a
    boost slot was a fixed 5 V buck. Every board needing a boost therefore spent a full
    repair cycle discovering what the planner already knew. Carried as a constraint, it
    becomes a spec filter on the distributor's own `Topology` parameter.

    `category` is the same lesson learned on the peripheral side, where it had been
    learned zero times: a sensor slot returned six connectors because "Environmentally"
    appears in RoHS boilerplate. See `parts.categories`.

    Both are vocabularies the engine owns, and a name outside one is dropped rather than
    repaired — a slot with no constraint searches exactly as it does today.
    """
    constraint: dict[str, str] = {}

    topology = str(entry.get("topology", "")).strip().lower()
    if topology in PLANNABLE_TOPOLOGIES:
        constraint["topology"] = topology

    category = str(entry.get("category", "")).strip().lower()
    if category in categories.CATEGORIES:
        constraint["category"] = category

    return constraint or None


def _normalise_slot_id(slot_id: str) -> str | None:
    """Make an otherwise valid id CSS-safe when only its first character is invalid.

    The ``s_`` prefix changes an identifier, not an engineering fact. Anything with an
    invalid later character (or too long after prefixing) is still dropped.
    """
    if not slot_id:
        return None
    if SLOT_ID.match(slot_id):
        return slot_id
    normalised = f"s_{slot_id}"
    return normalised if SLOT_ID.match(normalised) else None


def _clean_slots(raw: Any) -> tuple[dict[str, Slot], dict[str, str], dict[str, str]]:
    """Keep the slots that are well-formed. Drop the rest rather than repairing them."""
    slots: dict[str, Slot] = {}
    queries: dict[str, str] = {}
    renamed: dict[str, str] = {}

    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, Mapping):
            continue
        original_id = str(entry.get("id", "")).strip().lower()
        slot_id = _normalise_slot_id(original_id)
        tier = str(entry.get("tier", "")).strip().lower()
        if slot_id is None or slot_id in slots or tier not in TIERS:
            continue

        label = str(entry.get("label") or slot_id.replace("_", " ").title()).strip()
        query = str(entry.get("query") or label).strip()
        # The planner's prompt defines pinning as a user-named function and says power
        # and passive slots are implementation details. Enforce that judgement here so
        # a prompt cannot pin every repairable implementation choice; reverse this if
        # the product later lets users explicitly name one of those details.
        pinned = bool(entry.get("pinned")) and tier not in {"power", "passives"}
        slots[slot_id] = Slot(
            id=slot_id,
            label=label[:40],
            tier=tier,  # type: ignore[arg-type]
            pinned=pinned,
            constraint=_slot_constraint(entry),
        )
        queries[slot_id] = query[:60]
        if slot_id != original_id:
            renamed[original_id] = slot_id
        if len(slots) >= MAX_SLOTS:
            break

    return slots, queries, renamed


def _clean_rails(
    raw: Any, slots: Mapping[str, Slot], renamed: Mapping[str, str]
) -> list[Rail]:
    """Keep rails whose source and members are slots that actually exist.

    A rail naming a slot the planner did not declare would put a phantom into the
    engine's topology, and every rule keyed on that rail would then be reasoning about
    a part that is never placed.
    """
    rails: list[Rail] = []
    seen: set[str] = set()

    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, Mapping):
            continue
        rail_id = str(entry.get("id", "")).strip()
        voltage = entry.get("voltage")
        source = str(entry.get("source", "")).strip().lower()
        source = renamed.get(source, source)
        if not rail_id or rail_id in seen or not isinstance(voltage, (int, float)):
            continue
        if voltage <= 0 or source not in slots:
            continue

        members = tuple(
            m
            for m in (
                renamed.get(member, member)
                for member in (str(x).strip().lower() for x in entry.get("members") or [])
            )
            if m in slots and m != source
        )
        if not members:
            continue

        seen.add(rail_id)
        rails.append(Rail(id=rail_id, voltage=float(voltage), source=source, members=members))

    return rails


def _clean_links(
    raw: Any, slots: Mapping[str, Slot], renamed: Mapping[str, str]
) -> tuple[tuple[str, str], ...]:
    links: list[tuple[str, str]] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        master, peripheral = (
            renamed.get(slot_id, slot_id)
            for slot_id in (str(x).strip().lower() for x in entry)
        )
        if master in slots and peripheral in slots and master != peripheral:
            links.append((master, peripheral))
    return tuple(dict.fromkeys(links))


def _clean_requirements(raw: Mapping[str, Any]) -> Requirements:
    """Declared fields only, each checked against its own vocabulary or range."""
    defaults = Requirements()

    source = str(raw.get("input_source", "")).strip().lower()
    if source not in INPUT_SOURCES:
        source = UNRESOLVED

    temp = raw.get("temp_range")
    temp_range = defaults.temp_range
    if (
        isinstance(temp, (list, tuple))
        and len(temp) == 2
        and all(isinstance(t, (int, float)) for t in temp)
        and temp[0] < temp[1]
    ):
        temp_range = (int(temp[0]), int(temp[1]))

    margin = raw.get("current_margin")
    priority = str(raw.get("priority", "")).strip().lower()
    stock = raw.get("min_stock")
    ambient = raw.get("ambient_c")
    volts = raw.get("input_voltage")

    stated_margin = (
        float(margin) if isinstance(margin, (int, float)) and 0 < margin <= 1
        else defaults.current_margin
    )

    # Bounded like `input_voltage`, and for the same reason: it is an operand. A century
    # is not a design requirement, and a negative one is a misread rather than a brief.
    lifetime = raw.get("lifetime_hours")
    lifetime_hours = (
        float(lifetime)
        if isinstance(lifetime, (int, float)) and 0 < lifetime <= 24 * 365 * 50
        else None
    )

    return Requirements(
        temp_range=temp_range,
        current_margin=(
            max(stated_margin, BATTERY_MARGIN) if source in BATTERY_SOURCES else stated_margin
        ),
        input_source=source,
        # A stated voltage outranks the classification — see `topology.power_source`.
        # Bounded because it is an operand: nothing on a PCB runs at 10 kV, and an
        # absurd figure is likelier a misread than a design.
        input_voltage=(
            float(volts) if isinstance(volts, (int, float)) and 0 < volts <= 1000 else None
        ),
        priority=priority if priority in PRIORITIES else defaults.priority,  # type: ignore[arg-type]
        min_stock=int(stock) if isinstance(stock, int) and stock >= 0 else defaults.min_stock,
        ambient_c=int(ambient) if isinstance(ambient, (int, float)) else defaults.ambient_c,
        lifetime_hours=lifetime_hours,
        # Resolved here rather than in the engine, which imports no vocabulary of its own.
        supply_capacity_mah=capacity_of(source),
    )


def placement_order(slots: Mapping[str, Slot]) -> tuple[str, ...]:
    """Power, then the controller, then everything that hangs off it.

    Not cosmetic: a rail's regulator has to be placed before the parts drawing from it,
    or the first current check runs against a source that does not exist yet.

    Within a tier the planner's own order is kept — Python's sort is stable, and the
    order it listed things in carries intent. Sorting alphabetically instead would make
    placement depend on what the slots happen to be *named*.
    """
    rank = {"power": 0, "core": 1, "peripherals": 2, "passives": 3}
    return tuple(sorted(slots, key=lambda s: rank.get(slots[s].tier, 9)))


def _constrain_rail_sources(
    slots: dict[str, Slot], rails: Sequence[Rail], requirements: Requirements
) -> dict[str, Slot]:
    """Attach what each regulator must actually do, from the power tree already declared.

    The planner states rail voltages and the supply, then used to search for a regulator
    with no rating attached — so the 48 V board got a 40 V part and spent two repairs
    rediscovering its own arithmetic, and the 18650 board got a fixed 5 V part for a
    3.3 V rail and spent one.

    **Derived, not prompted.** Both numbers are already in the plan, so this needs no
    model compliance — the same reasoning that moved the battery margin out of the prompt.

    - `vout` — every rail's source has to be settable to that rail's voltage.
    - `vin_min` — only for regulators hanging off the *board input*. A cascade's second
      stage is fed by the first, whose output is not known until a part is chosen, so
      claiming an input rating for it would be inventing one.
    """
    supply = _supply_voltage(requirements)
    supplied = {member for rail in rails for member in rail.members}

    updated = dict(slots)
    for rail in rails:
        if rail.source not in updated:
            continue
        demands: dict[str, Any] = {"vout": rail.voltage}
        if supply is not None and rail.source not in supplied:
            demands["vin_min"] = supply
        slot = updated[rail.source]
        updated[rail.source] = replace(
            slot, constraint={**(slot.constraint or {}), **demands}
        )
    return updated


def _supply_voltage(requirements: Requirements) -> float | None:
    """What the board is fed, when that is known. `None` while the supply is unresolved."""
    if requirements.input_voltage is not None:
        return requirements.input_voltage
    source = INPUT_SOURCES.get(requirements.input_source)
    return None if source is None else source.voltage


def build_plan(raw: Mapping[str, Any]) -> Plan | None:
    """Validate a model reply into a `Plan`, or None if too little survived."""
    slots, queries, renamed = _clean_slots(raw.get("slots"))
    if len(slots) < 2:
        return None

    rails = _clean_rails(raw.get("rails"), slots, renamed)

    requirements = _clean_requirements(raw)
    slots = _constrain_rail_sources(slots, rails, requirements)

    return Plan(
        requirements=requirements,
        slots=slots,
        rails=rails,
        queries=queries,
        links=_clean_links(raw.get("links"), slots, renamed),
        order=placement_order(slots),
    )


# ── the call ──────────────────────────────────────────────────────────────────


async def plan_board(prompt: str) -> Plan | None:
    """Plan a board from a description. None when no usable plan came back."""
    if not llm.available():
        return None
    try:
        reply = await llm.complete_json(SYSTEM, prompt.strip()[:2000])
    except (llm.LLMUnavailable, ValueError, json.JSONDecodeError) as error:
        log.warning("planning failed: %s", error)
        return None

    plan = build_plan(reply)
    if plan is None:
        log.warning("planner returned nothing usable for %r", prompt[:60])
    return plan


def fallback_plan(prompt: str) -> Plan:
    """A board for when there is no model to plan one.

    Deliberately plain: one regulated rail, a controller, and whatever the prompt
    obviously mentions. It exists so the system still runs with no API key — the same
    degraded-but-honest mode the normaliser has — not so it can pretend to have planned.
    """
    text = prompt.lower()
    wanted: list[tuple[str, str, Tier, bool, str, str]] = [
        ("regulator", "Regulator", "power", False, "3.3V LDO regulator", "regulator"),
        ("mcu", "Microcontroller", "core", True, "ESP32 module", "mcu"),
    ]
    optional = [
        (("temp", "humid", "climate", "environment"), "sensor", "Temp / Humidity Sensor", "temperature humidity sensor", "sensor"),
        (("oled", "display", "screen", "readout"), "display", "Display", "OLED display", "display"),
        (("imu", "accelerom", "gyro", "motion"), "imu", "IMU", "IMU accelerometer", "sensor"),
        (("flash", "storage", "sd card", "memory"), "flash", "Serial Flash", "SPI NOR flash", "memory"),
    ]
    for keywords, slot_id, label, query, category in optional:
        if any(k in text for k in keywords):
            wanted.append((slot_id, label, "peripherals", True, query, category))

    # These slots are hardcoded, so their category is known rather than classified — and
    # this is the path a run takes when there is no key *or* when planning failed, which
    # is exactly when a wrong-category part is least likely to be noticed.
    slots = {
        slot_id: Slot(
            id=slot_id, label=label, tier=tier, pinned=pinned,
            constraint={"category": category},
        )
        for slot_id, label, tier, pinned, _, category in wanted
    }
    queries = {slot_id: query for slot_id, _, _, _, query, _ in wanted}
    loads = tuple(s for s in slots if s != "regulator")

    return Plan(
        requirements=_clean_requirements({"input_source": _guess_source(text)}),
        slots=slots,
        rails=[Rail(id="3V3", voltage=3.3, source="regulator", members=loads)],
        queries=queries,
        links=tuple(("mcu", s) for s in loads if s != "mcu"),
        order=placement_order(slots),
    )


def _guess_source(text: str) -> str:
    """Classify one unambiguous named supply, or say so. Never invent volts."""
    text = text.lower()
    usb = bool(re.search(r"\busb(?:[-\s]?c)?\b", text))
    liion = bool(re.search(r"\bli(?:[-\s]?ion|ion)\b", text))
    candidates = {
        name
        for pattern, name in (
            (r"\bcoin\s+cell\b", "battery-3v0"),
            (r"\bpoe\b", "poe"),
            (r"\b12\s*v\b", "12v-barrel"),
            (r"\b24\s*v\b", "24v-industrial"),
            (r"\b9\s*v\b", "9v-battery"),
        )
        if re.search(pattern, text)
    }
    if usb and liion:
        candidates.add("usb-5v+liion")
    elif usb:
        candidates.add("usb-5v")
    elif liion:
        candidates.add("battery-3v7")

    return candidates.pop() if len(candidates) == 1 else UNRESOLVED
