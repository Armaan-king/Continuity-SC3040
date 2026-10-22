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
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from .. import llm
from ..engine.models import Rail, Requirements, Slot, Tier
from ..parts import categories
from .topology import BATTERY_SOURCES, INPUT_SOURCES
from .requirements import explicit_requirements
from .prompt import system as planning_system
from .schema import PLAN_SCHEMA

log = logging.getLogger(__name__)
last_failure: ContextVar[str | None] = ContextVar("planning_failure", default=None)

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


SYSTEM = planning_system(categories.prompt_lines(), sorted(INPUT_SOURCES), MAX_SLOTS)

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
        supply_capacity_mah=(float(raw["supply_capacity_mah"])
            if isinstance(raw.get("supply_capacity_mah"), (int, float))
            and not isinstance(raw["supply_capacity_mah"], bool)
            and 0 < raw["supply_capacity_mah"] <= 10000000 else None),
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
    last_failure.set(None)
    if not llm.available():
        last_failure.set("AI is not configured for this server.")
        return None
    functions = requested_functions(prompt)
    request = prompt.strip()[:2000] + ("\nRequired functions: " + ", ".join(item[1] for item in functions) if functions else "")
    for attempt in range(2):
        try:
            reply = await llm.complete_json(SYSTEM, request, schema=PLAN_SCHEMA)
            # Preserve explicit requirements even if the planner confuses USB charging
            # with battery-powered operation. Never use a generic cell capacity.
            explicit = explicit_requirements(prompt)
            reply["supply_capacity_mah"] = explicit.get("supply_capacity_mah")
            reply.update(explicit)
            plan = build_plan(reply)
        except (llm.LLMUnavailable, ValueError, json.JSONDecodeError) as error:
            last_failure.set(str(error) if isinstance(error, llm.LLMUnavailable)
                             else "The model returned an invalid plan.")
            log.warning("planning failed: %s", error)
            return None
        issues = structural_issues(plan, prompt)
        if not issues:
            last_failure.set(None)
            return plan
        log.warning("planner structure rejected: %s", "; ".join(issues))
        last_failure.set("The model's plan failed structural validation.")
        if attempt == 0:
            request = (prompt.strip()[:2000] + "\nYour previous plan had these structural errors: "
                       + "; ".join(issues) + "\nReturn a complete corrected JSON plan.")
    return None


FUNCTIONS = (
    (r"\b(?:button|pushbutton)\b", "button", "Arm / Control Button", "passives", "tactile switch", "switch"),
    (r"\bled\b", "led", "Status LED", "passives", "indicator LED", "led"),
    (r"\bbuzzer\b", "buzzer", "Alarm Buzzer", "peripherals", "active buzzer", "audio"),
    (r"\b(?:usb[- ]?c?\s+charging|charge controller|battery charger)\b", "charger", "Battery Charger", "power", "Li-ion charger", "charger"),
)


def requested_functions(prompt: str) -> list[tuple]:
    """Protect explicitly named support functions often dropped by compact plans."""
    return [item for item in FUNCTIONS if re.search(item[0], prompt, re.I)
            and not re.search(r"\b(?:no|without)\s+(?:an?\s+)?" + item[0], prompt, re.I)]


def structural_issues(plan: Plan | None, prompt: str = "") -> list[str]:
    """Reject incomplete wiring before sourcing; electrical verdicts stay in the engine."""
    if plan is None:
        return ["At least two valid functional slots are required"]
    issues = []
    descriptions = " ".join(f"{slot.id} {slot.label} {plan.queries.get(slot.id, '')} {(slot.constraint or {}).get('category', '')}" for slot in plan.slots.values()).lower()
    for _, key, label, _, _, _ in requested_functions(prompt):
        patterns = {"button": r"button|switch|keypad", "led": r"\bled\b|indicator", "buzzer": r"buzzer|sounder|speaker", "charger": r"charg"}
        if not re.search(patterns[key], descriptions):
            issues.append(f"Include a separate {label} slot explicitly requested in the brief")
    if not plan.rails:
        issues.append("Declare power rails and their regulator/charger source slots")
    supplied = {member for rail in plan.rails for member in rail.members}
    for rail in plan.rails:
        source = plan.slots[rail.source]
        if plan.requirements.input_source in BATTERY_SOURCES and (source.constraint or {}).get("category") == "charger":
            issues.append(f"Battery operation cannot use charger {source.id} as a constant supply; model the load regulator from the battery")
        if source.tier != "power":
            issues.append(f"Rail {rail.id} source {source.id} must be a separate power slot, not a load")
    for slot in plan.slots.values():
        if slot.tier in {"core", "peripherals"} and slot.id not in supplied:
            issues.append(f"Connect {slot.id} to a declared power rail")
    return issues


def fallback_plan(prompt: str) -> Plan:
    """A board for when there is no model to plan one.

    Deliberately plain: one regulated rail, a controller, and whatever the prompt
    obviously mentions. It exists so the system still runs with no API key — the same
    degraded-but-honest mode the normaliser has — not so it can pretend to have planned.
    """
    text = prompt.lower()
    # Operating conditions are not requests for a temperature-sensing component.
    functions_text = re.sub(r"\b(?:operating|ambient|storage)\s+temperature(?:\s+range)?\b", "", text)
    wanted: list[tuple[str, str, Tier, bool, str, str]] = [
        ("regulator", "Regulator", "power", False, "3.3V LDO regulator", "regulator"),
        ("mcu", "Microcontroller", "core", True, "ESP32 module", "mcu"),
    ]
    optional = [
        (r"\b(?:temp|temperature|humidity|climate|environment|environmental)\b", "sensor", "Temp / Humidity Sensor", "temperature humidity sensor", "sensor"),
        (r"\b(?:ambient[- ]light|light sensor|plant[- ]light)\b", "light", "Ambient Light Sensor", "ambient light sensor", "sensor"),
        (r"\b(?:oled|display|screen|readout)\b", "display", "Display", "OLED display", "display"),
        (r"\b(?:imu|accelerometer|gyroscope|gyro|motion)\b", "imu", "IMU", "IMU accelerometer", "sensor"),
        (r"\b(?:flash|storage|sd card|memory)\b", "flash", "Serial Flash", "SPI NOR flash", "memory"),
    ]
    optional.extend([
        (r"\blora\b", "lora", "LoRa Radio", "LoRa module", "radio"),
        (r"\b(?:gps|gnss)\b", "gps", "GNSS Receiver", "GNSS module", "radio"),
    ])
    for pattern, slot_id, label, query, category in optional:
        if re.search(pattern, functions_text):
            wanted.append((slot_id, label, "peripherals", True, query, category))

    for _, slot_id, label, tier, query, category in requested_functions(prompt):
        wanted.append((slot_id, label, tier, True, query, category))

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
    # The charger requires its own input and battery path. Leave those unmodelled
    # for review rather than pretending it is another load on the logic rail.
    loads = tuple(s.id for s in slots.values() if s.tier != "power")

    return Plan(
        requirements=_clean_requirements({"input_source": _guess_source(text), **explicit_requirements(text)}),
        slots=slots,
        rails=[Rail(id="3V3", voltage=3.3, source="regulator", members=loads)],
        queries=queries,
        links=tuple(("mcu", s) for s in loads if s in {"sensor", "light", "display", "imu", "flash", "lora", "gps"}),
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
    if explicit_requirements(text).get("input_source"):
        return explicit_requirements(text)["input_source"]
    if usb and liion:
        candidates.add("usb-5v+liion")
    elif usb:
        candidates.add("usb-5v")
    elif liion:
        candidates.add("battery-3v7")

    return candidates.pop() if len(candidates) == 1 else UNRESOLVED
