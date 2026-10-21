"""Validate a user-supplied BOM without invoking the design planner."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import Any, AsyncIterator, Callable, Mapping, Sequence

from pydantic import AliasChoices, BaseModel, Field

from .. import llm
from ..engine import rules
from ..engine.models import Board, PartSpec, Rail, Requirements, Slot, Tier, Verdict
from ..parts import normalize, search
from ..planner import plan as planner
from ..planner import topology
from . import events


log = logging.getLogger(__name__)

MAX_BOM_ROWS = 100

PlacedPartsObserver = Callable[[Sequence[PartSpec]], None]
"""Lets the API persist stable facts without putting an API concern in an event frame."""

_placed_parts_observer: ContextVar[PlacedPartsObserver | None] = ContextVar(
    "placed_parts_observer", default=None
)


def set_placed_parts_observer(observer: PlacedPartsObserver | None) -> Token[PlacedPartsObserver | None]:
    """Inject a request-local observer for the `PartSpec`s this validation already built."""
    return _placed_parts_observer.set(observer)


def reset_placed_parts_observer(token: Token[PlacedPartsObserver | None]) -> None:
    """Restore the observer inherited by an enclosing request, if any."""
    _placed_parts_observer.reset(token)


class BomItem(BaseModel):
    mpn: str
    qty: int = 1
    reference: str | None = Field(
        default=None,
        validation_alias=AliasChoices("reference", "reference_designator"),
    )


class BomRequest(BaseModel):
    bom: str | list[BomItem]
    prompt: str | None = None
    """What the user typed alongside the file. Named to match `DesignRequest.prompt`:
    both endpoints take the same thing from the same box, and two names for it would be
    a papercut for anyone reading the API. Internally it is a *brief*, which is what the
    requirement inference calls it."""

    project_id: str | None = None


@dataclass(frozen=True)
class BomRow:
    mpn: str
    qty: int = 1
    reference: str | None = None


class BomInputError(ValueError):
    pass


RAIL_SYSTEM = """You infer only a power tree from an existing PCB bill of materials.
Return one JSON object with exactly one key: rails.

Each rails item may have only: id, voltage, source, members.
source and every members entry must be one of the supplied BOM row ids. Do not name a
part that is absent, do not create a rail if the BOM does not support one, and return
an empty rails array when you cannot infer a useful tree. voltage is the only numeric
design decision you may state. Never state or infer any component rating, current,
temperature, package, or requirement: those are supplied by another system.
"""


REQUIREMENT_FIELDS = (
    "input_source",
    "input_voltage",
    "temp_range",
    "current_margin",
    "priority",
    "min_stock",
    "ambient_c",
)


REQUIREMENTS_SYSTEM = f"""You infer only board-level validation requirements from a user's brief.
Return one JSON object with only these keys: {", ".join(REQUIREMENT_FIELDS)}.

Do not return slots, rails, links, parts, MPNs, component descriptions, component
ratings, packages, or anything inferred from components. No components are supplied to
you. Requirements come only from what the user said in the brief.

input_source: choose the closest of: {", ".join(sorted(planner.INPUT_SOURCES))}. Use
"{planner.UNRESOLVED}" if none fits.
input_voltage: a voltage in volts only when the brief states or directly implies it;
otherwise null. Never invent a voltage.
temp_range: [min, max] in Celsius. Commercial is [0, 70]; industrial is [-40, 85].
current_margin: 0.15 normally, 0.30 for battery or low-power designs.
priority: one of {", ".join(planner.PRIORITIES)}.
min_stock: units the user must be able to buy; 100 unless the brief states a production
volume.
ambient_c: expected ambient temperature; 25 unless the brief states it.

Return the JSON object and nothing else."""


def _default_requirements_payload() -> dict[str, Any]:
    defaults = Requirements()
    return {field: getattr(defaults, field) for field in REQUIREMENT_FIELDS}


async def requirements_from_brief(brief: str | None) -> Requirements:
    """Classify a user's stated requirements, never the BOM's component needs."""
    defaults = Requirements()
    if not brief or not brief.strip():
        return defaults
    if not llm.available():
        log.warning("BOM requirements inference unavailable; using defaults")
        return defaults
    try:
        reply = await llm.complete_json(REQUIREMENTS_SYSTEM, brief.strip()[:2000])
    except (llm.LLMUnavailable, ValueError, json.JSONDecodeError) as error:
        log.warning("BOM requirements inference failed; using defaults: %s", error)
        return defaults
    if not isinstance(reply, Mapping):
        log.warning("BOM requirements inference returned no usable object; using defaults")
        return defaults

    raw = _default_requirements_payload()
    supplied = False
    for field in REQUIREMENT_FIELDS:
        if field in reply:
            raw[field] = reply[field]
            supplied = True
    if not supplied:
        log.warning("BOM requirements inference returned no requirement fields; using defaults")
        return defaults
    try:
        return planner._clean_requirements(raw)
    except (OverflowError, TypeError, ValueError) as error:
        log.warning("BOM requirements inference returned unusable fields; using defaults: %s", error)
        return defaults


def parse_bom(value: str | list[BomItem]) -> list[BomRow]:
    """Parse either structured rows, CSV, or one MPN per non-empty line."""
    if isinstance(value, list):
        rows = [BomRow(item.mpn.strip(), item.qty, item.reference) for item in value]
    else:
        rows = _parse_pasted_bom(value)

    if len(rows) > MAX_BOM_ROWS:
        raise BomInputError(f"BOM accepts at most {MAX_BOM_ROWS} rows")
    cleaned = [row for row in rows if row.mpn]
    if not cleaned:
        raise BomInputError("BOM must contain at least one MPN")
    for row in cleaned:
        if row.qty < 1:
            raise BomInputError(f"quantity for {row.mpn} must be at least 1")
    return cleaned


def _parse_pasted_bom(text: str) -> list[BomRow]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    parsed = list(csv.reader(io.StringIO("\n".join(lines))))
    header = [cell.strip().lower() for cell in parsed[0]] if parsed else []
    if "mpn" not in header:
        return [BomRow(row[0].strip()) for row in parsed if row]

    indexes = {"mpn": header.index("mpn")}
    if "qty" in header:
        indexes["qty"] = header.index("qty")
    elif "quantity" in header:
        indexes["qty"] = header.index("quantity")
    if "reference" in header:
        indexes["reference"] = header.index("reference")
    elif "reference_designator" in header:
        indexes["reference"] = header.index("reference_designator")
    rows: list[BomRow] = []
    for values in parsed[1:]:
        mpn = _cell(values, indexes["mpn"]).strip() if "mpn" in indexes else ""
        qty_text = _cell(values, indexes["qty"]).strip() if "qty" in indexes else "1"
        try:
            qty = int(qty_text or "1")
        except ValueError as error:
            raise BomInputError(f"quantity for {mpn or 'BOM row'} must be an integer") from error
        reference = _cell(values, indexes["reference"]).strip() if "reference" in indexes else None
        rows.append(BomRow(mpn, qty, reference or None))
    return rows


def _cell(values: list[str], index: int) -> str:
    return values[index] if index < len(values) else ""


async def infer_rails(resolved: list[tuple[str, search.Candidate]]) -> list[Mapping[str, Any]]:
    """Ask the model only to classify the BOM's already-resolved parts into rails."""
    if not resolved or not llm.available():
        return []
    payload = [
        {"id": slot_id, "mpn": candidate.mpn, "description": candidate.description}
        for slot_id, candidate in resolved
    ]
    try:
        reply = await llm.complete_json(RAIL_SYSTEM, json.dumps({"parts": payload}))
    except (llm.LLMUnavailable, ValueError, json.JSONDecodeError) as error:
        log.warning("BOM rail inference failed: %s", error)
        return []
    rails = reply.get("rails") if isinstance(reply, Mapping) else None
    return [rail for rail in rails if isinstance(rail, Mapping)] if isinstance(rails, list) else []


async def validate_bom(
    rows: list[BomRow], stream: events.EventStream, brief: str | None = None
) -> AsyncIterator[dict[str, Any]]:
    """Resolve, type, infer a guarded tree, and narrate engine verdicts."""
    started_at = time.time()
    requirements = await requirements_from_brief(brief)
    resolved = await asyncio.gather(*(_resolve(row) for row in rows))
    candidates = [(index, result) for index, result in enumerate(resolved) if result is not None]
    parts = await asyncio.gather(*(_normalise(candidate) for _, candidate in candidates))
    by_index = {index: part for (index, _), part in zip(candidates, parts) if part is not None}
    observer = _placed_parts_observer.get()
    if observer is not None:
        observer(tuple(by_index.values()))

    slots = {
        _slot_id(index): Slot(
            id=_slot_id(index),
            label=(row.reference or row.mpn)[:40],
            tier=_tier_for_part(by_index.get(index)),
            part=by_index.get(index),
            status="pending",
        )
        for index, row in enumerate(rows)
    }
    resolved_for_inference = [
        (_slot_id(index), candidate)
        for index, candidate in candidates
        if by_index.get(index) is not None
    ]
    aliases = _unique_aliases(rows, [(index, candidate) for index, candidate in candidates if by_index.get(index) is not None])
    declared = _weighted_rails(
        planner._clean_rails(await infer_rails(resolved_for_inference), slots, aliases), rows
    )
    board = Board(requirements, slots, {rail.id: rail for rail in declared})

    yield stream.plan(slots.values(), topology.power_edges(board.rails))

    unresolved = [
        Verdict(
            rule="availability",
            status="warn",
            detail=(
                f"No part found for BOM MPN {row.mpn}."
                if resolved[index] is None
                else f"BOM MPN {row.mpn} was found but its specifications could not be read."
            ),
            unchecked=resolved[index] is not None,
            subject=_slot_id(index),
            involved=(_slot_id(index),),
        )
        for index, row in enumerate(rows)
        if by_index.get(index) is None
    ]
    verdicts = [*unresolved, *rules.evaluate(board)]
    if not board.rails:
        verdicts.extend(_unchecked_rail_verdicts(slots))

    # A row's status has to say whether it *failed*, not merely that it was resolved.
    # These were all emitted as "pending" before the checks ran, and the frontend counts
    # conflicts by slot status — so a BOM with two real temperature failures rendered as
    # "0 conflict" with every node still the colour of a part being searched for. The
    # verdicts exist by now, so the status is knowable before the row is announced.
    failing = {verdict.subject for verdict in rules.failures(verdicts)}
    for index, part in by_index.items():
        slot_id = _slot_id(index)
        yield stream.selection(slot_id, part, "conflict" if slot_id in failing else "pass")

    for verdict in verdicts:
        yield stream.check(verdict)
    for verdict in rules.failures(verdicts):
        yield stream.conflict(verdict, edge=verdict.scope)

    bom_rows = [events.bom_row(_slot_id(index), part, rows[index].qty) for index, part in by_index.items()]
    yield stream.bom(bom_rows, sum((part.unit_price or 0.0) * rows[index].qty for index, part in by_index.items()))
    yield stream.done(
        slots=len(slots),
        placed=len(by_index),
        conflicts_resolved=0,
        elapsed_s=time.time() - started_at,
    )


async def _resolve(row: BomRow) -> search.Candidate | None:
    try:
        payload = await search.get_part(mpn=row.mpn)
    except Exception as error:  # a lookup outage remains a finding about this row
        log.warning("BOM lookup failed for %s: %s", row.mpn, error)
        return None
    results = payload.get("results") if isinstance(payload, Mapping) else None
    if not isinstance(results, list):
        return None
    for result in results:
        if isinstance(result, Mapping) and result.get("model"):
            return search._candidate(result)
    return None


async def _normalise(candidate: search.Candidate):
    try:
        return await normalize.normalize(candidate)
    except Exception as error:  # one malformed result must not hide the rest of a BOM
        log.warning("BOM normalisation failed for %s: %s", candidate.mpn, error)
        return None


def _weighted_rails(rails: list[Rail], rows: list[BomRow]) -> list[Rail]:
    """Represent a row quantity as repeated consumers for the unmodified rule engine."""
    quantities = {_slot_id(index): row.qty for index, row in enumerate(rows)}
    return [
        replace(rail, members=tuple(member for member in rail.members for _ in range(quantities[member])))
        for rail in rails
    ]


def _unchecked_rail_verdicts(slots: Mapping[str, Slot]) -> list[Verdict]:
    """Make a missing power tree visible; the rules themselves have no rail to visit."""
    subject = next(iter(slots), "bom")
    return [
        Verdict(
            rule=rule,
            status="warn",
            detail=f"No usable power rails were inferred from this BOM — {label} is unchecked.",
            unchecked=True,
            subject=subject,
            involved=(subject,),
        )
        for rule, label in (
            ("voltage_overlap", "voltage compatibility"),
            ("current_budget", "current budget"),
            ("thermal_dissipation", "thermal dissipation"),
        )
    ]


def _slot_id(index: int) -> str:
    return f"bom_{index + 1}"


def _tier_for_part(part: PartSpec | None) -> Tier:
    if part is None:
        return "peripherals"
    if part.regulation is not None:
        return "power"
    if part.role == "master":
        return "core"
    if part.role == "peripheral":
        return "peripherals"
    if part.role == "passive":
        return "passives"
    return "peripherals"


def _unique_aliases(
    rows: list[BomRow], candidates: list[tuple[int, search.Candidate]]
) -> dict[str, str]:
    """Allow an inference to name a unique MPN, never an unresolved or ambiguous row."""
    aliases: dict[str, list[str]] = {}
    for index, candidate in candidates:
        aliases.setdefault(candidate.mpn.strip().lower(), []).append(_slot_id(index))
        reference = rows[index].reference
        if reference:
            aliases.setdefault(reference.strip().lower(), []).append(_slot_id(index))
    return {name: ids[0] for name, ids in aliases.items() if len(ids) == 1}
