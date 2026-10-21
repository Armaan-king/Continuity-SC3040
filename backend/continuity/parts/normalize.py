"""Distributor payload → `PartSpec`, with provenance.

JLCPCB gives a controlled parameter vocabulary with free-text values:

    "Voltage - Supply": "1.08V~3.6V"      a range
    "Voltage - Supply": "15V"             a maximum, on a different part
    "Current - Supply": "400nA"           units vary by five orders of magnitude
    "Operating Temperature": "-40℃~+125℃"  non-ASCII degree signs

One LLM call per candidate turns that into typed fields. What it may fill is fixed —
see `PARSED_FIELDS` — and everything it returns is validated against that list before
any of it reaches a rule.

## What the model is not allowed to do

- **Add a field.** Unknown keys are dropped, not merged.
- **Change a type.** A string where a number belongs is dropped.
- **Invent provenance.** A `provenance` entry naming a parameter that is not in the
  payload is dropped, so evidence can never cite a field the distributor never sent.
- **Touch anything commercial.** `stock`, `price`, `package`, `mpn` are read straight
  from the payload. Those are already typed; handing them to a model would add a way
  to be wrong and no way to be right.

## Cached by MPN, for determinism rather than cost

An LLM that reads `"2.5V ~ 6.0V"` one way in rehearsal and another way on stage changes
the demo underneath you. The cache freezes the parse the first time it happens.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from .. import env, llm
from ..engine.models import PartSpec
from . import categories, datasheet, dossier, payload, search
from .search import Candidate

log = logging.getLogger(__name__)

CACHE_DIR = env.cache_dir("normalized")
THERMAL_FETCH_TASKS: set[asyncio.Task[None]] = set()

DossierLookup = Callable[[str], Awaitable[list[dict[str, Any]]]]
"""An optional persistence boundary; parts never need to know who owns the database."""

_dossier_lookup: ContextVar[DossierLookup | None] = ContextVar("dossier_lookup", default=None)


def set_dossier_lookup(lookup: DossierLookup | None) -> Token[DossierLookup | None]:
    """Inject a request-local lookup for callers that cannot change graph node signatures."""
    return _dossier_lookup.set(lookup)


def reset_dossier_lookup(token: Token[DossierLookup | None]) -> None:
    """Restore the caller's lookup once its request has finished."""
    _dossier_lookup.reset(token)

PARSED_FIELDS: dict[str, type] = {
    "vmin": float,
    "vmax": float,
    "vout_min": float,
    "vout_max": float,
    "i_typ": float,
    "i_peak": float,
    "i_max": float,
    "role": str,
    "pins_required": int,
    "pins_available": int,
    "topology": str,
    "synchronous": bool,
    "efficiency": float,
    "temp_min": float,
    "temp_max": float,
}
"""Everything the model may fill. `interfaces` is handled separately — it is a list."""

ROLES = {"master", "peripheral", "passive"}

GPIO_PIN = re.compile(r"^(GPIO|IO|P[A-F])\d+", re.I)
"""A general-purpose pin, by name.

Raw pin count is not GPIO count — an ESP32-WROOM has 39 pins and 22 usable IO, the rest
being GND, 3V3, EN and NC. R3 budgets the ones a peripheral can actually be wired to,
so counting the package's pins would overstate the budget by nearly half.
"""


def gpio_count(pin_names: tuple[str, ...]) -> int | None:
    """How many pins a peripheral could be attached to, or None if unknowable."""
    if not pin_names:
        return None
    return sum(1 for name in pin_names if GPIO_PIN.match(name)) or None


TOPOLOGIES = {"ldo", "buck", "boost", "buck-boost", "sepic", "switching", "linear"}

SYSTEM = """You convert electronic component parameters into typed fields.

You are given a distributor's parameter block for one part. Return ONE JSON object.

Rules:
- Use ONLY these keys: vmin, vmax, vout_min, vout_max, i_typ, i_peak, i_max, interfaces,
  role, pins_required, pins_available, topology, synchronous, efficiency, temp_min, temp_max,
  provenance.
- Every value is in BASE SI UNITS: volts, amps, degrees Celsius. 400nA is 0.0000004.
  1.5mA is 0.0015. Never return a value with a unit suffix.
- MEASUREMENTS must be READ from the parameter block. Use null when it does not state
  one. Never guess, never infer a typical value, never copy a number from a similar
  part you know. This applies to every numeric field without exception.
- CATEGORIES may come from what the part IS when the block is silent, and only these
  two: role and interfaces. Distributors publish no bus column for microcontrollers,
  so an MCU whose block mentions no bus still speaks the ones its family is defined by.
  This is a statement about what the part is, not a measurement of it.
- vmin/vmax are the part's SUPPLY range. "1.08V~3.6V" means vmin 1.08, vmax 3.6.
  A single figure like "15V" on a regulator is a MAXIMUM: vmax 15, vmin null.
- vout_min/vout_max and i_max apply to regulators only. vout_min/vout_max are the
  OUTPUT range, and "Output Type" tells you how to read it:
    Output Type "Fixed",      "Output Voltage: 3.3V"    → vout_min 3.3, vout_max 3.3
    Output Type "Adjustable", "Output Voltage: 1.23V~37V" → vout_min 1.23, vout_max 37
    Output Type "Adjustable", "Output Voltage: 28V"    → vout_max 28, vout_min NULL
  That last case matters: an adjustable part quoting one figure is stating its ceiling,
  not its output. Reporting 28 as both ends says the part can only ever make 28 V.
- i_typ is typical/quiescent supply current, i_peak the maximum.
- role: "master" for MCUs and anything driving a bus, "peripheral" for sensors,
  displays and memory, "passive" for regulators, resistors, capacitors.
- topology: one of ldo, buck, boost, buck-boost or sepic. Only for regulators. A field
  may state more than one topology separated by ',' or '、'; read every stated value.
- synchronous: read the regulator's "Synchronous Rectifier" parameter only. Yes means
  true, No means false, and '-' means not stated (null). Only for regulators.
- efficiency: 0-1 fraction, switchers only. "92%" is 0.92.
- interfaces: array of bus names the part can use, uppercase, e.g. ["I2C","SPI","UART"].
  Read "Interface Type" when it is given. When it is absent and the part is a controller,
  list the buses that part family provides. [] only when the part truly has no bus —
  a regulator, a capacitor, a discrete.
- pins_available: usable GPIO on a controller. Read "Number of I/O". NEVER take the
  package pin count: LQFP-48 has 48 pins, several of which are VDD, VSS and NRST.
- provenance: object mapping each field you filled to the EXACT parameter name you
  read it from. Only include fields you actually filled from a parameter — omit the
  ones you knew from the part's identity rather than read.

Return the JSON object and nothing else."""


# ── the call ──────────────────────────────────────────────────────────────────


def _prompt(candidate: Candidate) -> str:
    return json.dumps(
        {
            "mpn": candidate.mpn,
            "category": candidate.subcategory or candidate.category,
            "package": candidate.package,
            "description": candidate.description,
            "parameters": dict(candidate.specs),
        },
        ensure_ascii=False,
        indent=1,
    )


def _coerce(name: str, value: Any) -> Any | None:
    """Return the value only if it is the declared type. Otherwise drop it."""
    if value is None:
        return None
    expected = PARSED_FIELDS[name]
    if expected is bool:
        return value if isinstance(value, bool) else None
    if isinstance(value, bool):
        return None  # bools are ints in Python; never a legitimate answer here
    if expected is float and isinstance(value, (int, float)):
        return float(value)
    if expected is int and isinstance(value, int):
        return value
    if expected is str and isinstance(value, str):
        return value.strip().lower() or None
    return None


def _resolve_output_range(
    raw: Mapping[str, Any], fields: dict[str, Any], specs: Mapping[str, str]
) -> None:
    """Settle `vout_min`/`vout_max` into a range that means something, in place.

    What one output figure means depends entirely on `Output Type`. A **fixed** part
    quoting 3.3 V is fixed at it, so both ends collapse — otherwise it would look
    adjustable-up-to-3.3 and be accepted for a 1.8 V rail. An **adjustable** part quoting
    28 V is telling you its ceiling, and its floor is unpublished; collapsing that made
    `produces(3.3)` return False and rejected the exact part a boost repair needs.

    A reversed range is dropped whole. It is likelier a misread parameter than a real
    part, and half of a wrong range is worse than none.
    """
    adjustable = payload.is_adjustable(specs)

    if "vout_min" not in fields and "vout_max" not in fields:
        single = _coerce("vout_max", raw.get("vout"))
        if single is not None:
            fields["vout_max"] = single
            if not adjustable:
                fields["vout_min"] = single
        return

    low, high = fields.get("vout_min"), fields.get("vout_max")
    if low is not None and high is not None:
        if low > high:
            del fields["vout_min"], fields["vout_max"]
        elif low == high and adjustable:
            # One figure reported as both ends. For an adjustable part that is a ceiling.
            del fields["vout_min"]
    elif low is not None and not adjustable:
        fields["vout_max"] = low


def validate(raw: Mapping[str, Any], specs: Mapping[str, str]) -> tuple[dict, dict]:
    """Filter a model reply down to declared fields with usable values.

    Returns `(fields, provenance)`. Anything unrecognised, mistyped, or citing a
    parameter the distributor never sent is discarded silently — the engine will then
    report the field as unstated, which is the correct outcome for a value we could
    not establish.
    """
    fields: dict[str, Any] = {}

    for name in PARSED_FIELDS:
        coerced = _coerce(name, raw.get(name))
        if coerced is None:
            continue
        if name == "role" and coerced not in ROLES:
            continue
        if name == "topology" and (
            not (values := [value.strip() for value in re.split(r"[、,]", coerced)])
            or any(value not in TOPOLOGIES for value in values)
        ):
            continue
        if name == "efficiency" and not 0 < coerced <= 1:
            continue
        fields[name] = coerced

    _resolve_output_range(raw, fields, specs)

    interfaces = raw.get("interfaces")
    if isinstance(interfaces, list):
        fields["interfaces"] = tuple(
            str(bus).strip().upper() for bus in interfaces if isinstance(bus, str) and bus.strip()
        )

    provenance: dict[str, str] = {}
    for field, parameter in (raw.get("provenance") or {}).items():
        # A citation is only worth anything if it points at something really sent.
        if field in fields and isinstance(parameter, str) and parameter in specs:
            provenance[field] = parameter

    return fields, provenance


def _discard_non_regulator_fields(
    fields: dict[str, Any], provenance: dict[str, str], category: str
) -> None:
    """Remove regulator measurements from a part the distributor categorised otherwise."""
    if not category or category in categories.CATEGORIES["regulator"].accepts:
        return
    # Motor drivers share the PMIC category, so they may retain topology. That is safe:
    # only a rail source reads it, and stripping it would reject real regulators too.
    for name in ("topology", "vout_min", "vout_max", "i_max", "synchronous", "efficiency"):
        fields.pop(name, None)
        provenance.pop(name, None)


def _from_payload(
    candidate: Candidate,
    datasheet: str | None,
    lifecycle: str | None,
    stock: int | None,
    dossier_fields: Mapping[str, tuple[Any, str | None]] | None = None,
) -> dict[str, Any]:
    """The fields that need no interpretation. Typed at the source; leave them alone."""
    fields = {
        "mpn": candidate.mpn,
        "manufacturer": candidate.manufacturer,
        "description": candidate.description,
        "category": candidate.subcategory or candidate.category,
        "package": candidate.package,
        "unit_price": candidate.unit_price,
        "currency": "USD",
        "stock": stock,
        "distributor": "JLCPCB",
        # Never asserted. R6 warns on nrnd and obsolete, so claiming "active" without a
        # source would silence that warning on every part and say nothing about why.
        "lifecycle": lifecycle or "unknown",
        # Also never asserted. JLCPCB publishes no lead time, and claiming 0 would put
        # "in stock, ships today" into an exported BOM for every part on the board —
        # a false claim in the one artifact somebody might actually order from.
        "lead_time_days": None,
        # JLCPCB publishes no datasheet link, so this is the ECAD index where it
        # resolves and the product page otherwise. Never a constructed URL: a source a
        # judge cannot open is worse than one that admits what it is.
        "datasheet": datasheet or candidate.product_url,
        "product_url": candidate.product_url,
        "raw": dict(candidate.specs),
    }
    # Package is the one dossier field that arrives typed directly from the listing. A
    # non-empty listing wins even when a previous run learned a different spelling.
    if not fields["package"] and dossier_fields is not None and "package" in dossier_fields:
        fields["package"] = dossier_fields["package"][0]
    return fields


async def _dossier_fields(
    candidate: Candidate, lookup: DossierLookup | None
) -> dict[str, tuple[Any, str | None]]:
    """Read only recognised, type-safe facts; a database miss is ordinary enrichment loss."""
    if lookup is None:
        return {}
    try:
        rows = await lookup(candidate.mpn)
    except Exception as error:  # a durable cache may be unavailable during a useful run
        log.warning("part dossier lookup failed for %s: %s", candidate.mpn, error)
        return {}
    fields: dict[str, tuple[Any, str | None]] = {}
    for row in rows:
        field = row.get("field")
        value = row.get("value")
        source = row.get("source")
        if not isinstance(field, str) or not isinstance(value, str):
            continue
        parsed = dossier.value_from_text(field, value)
        if parsed is not None:
            fields[field] = (parsed, source if isinstance(source, str) else None)
    return fields


async def _fetch_theta_ja(
    candidate: Candidate, enrichment: "asyncio.Task[search.Enrichment]"
) -> datasheet.ThermalFact | None:
    """Fetch and extract an unlisted θJA without letting an ordinary host failure escape."""
    try:
        extra = await enrichment
        if not extra.datasheet:
            # `product_url` is the JLCPCB part page, which is HTML by construction. Trying
            # it anyway spends a request per unknown-package part to be told what we
            # already know.
            return None
        data = await datasheet.fetch(extra.datasheet)
        if data is None:
            return None
        text = datasheet.text_from_pdf(data)
        if text is None:
            return None
        return await datasheet.theta_ja_from_text(
            text, mpn=candidate.mpn, package=candidate.package or ""
        )
    except Exception:  # a datasheet host must never break normalisation
        return None


def _start_theta_ja_fetch(candidate: Candidate, enrichment: "asyncio.Task[search.Enrichment]") -> None:
    """Leave a best-effort fact lookup running without delaying normalisation."""
    async def fetch_and_cache() -> None:
        await _fetch_theta_ja(candidate, enrichment)

    task = asyncio.create_task(fetch_and_cache())
    THERMAL_FETCH_TASKS.add(task)
    task.add_done_callback(THERMAL_FETCH_TASKS.discard)


async def normalize(
    candidate: Candidate,
    *,
    use_cache: bool = True,
    fetch_missing_theta_ja: bool = False,
    dossier_lookup: DossierLookup | None = None,
) -> PartSpec:
    """One candidate → one `PartSpec`. Cached by MPN.

    Degrades rather than fails: with no LLM configured, the payload fields are still
    populated and the parsed ones stay null, so R1 and R4 report *could not check*
    instead of the engine guessing.
    """
    # These lookups do not depend on the parse, and the parse does not depend on them,
    # so they run together. Serially they roughly doubled the time to place a part, and
    # a board is several parts deep.
    enrichment = asyncio.create_task(search.enrich(candidate.mpn))
    live_stock = asyncio.create_task(search.live_stock(candidate.mpn))
    thermal_fact = datasheet._load(candidate.mpn)
    if fetch_missing_theta_ja and thermal_fact is None:
        _start_theta_ja_fetch(candidate, enrichment)
    # Let both tasks issue their I/O before an immediately-returning test or local LLM
    # can finish the parse without ever yielding to the event loop.
    await asyncio.sleep(0)

    dossier_fields = await _dossier_fields(candidate, dossier_lookup or _dossier_lookup.get())

    cached = _load(candidate.mpn) if use_cache else None
    if cached is not None:
        fields, provenance = cached
    elif not llm.available():
        fields, provenance = {}, {}
    else:
        try:
            reply = await llm.complete_json(SYSTEM, _prompt(candidate))
            fields, provenance = validate(reply, candidate.specs)
            if use_cache:
                _save(candidate.mpn, fields, provenance)
        except (llm.LLMUnavailable, ValueError, json.JSONDecodeError) as error:
            # Degrade, but not quietly. With no key configured this is expected and
            # `llm.available()` already covered it above; reaching here means the call
            # was attempted and failed, and a parse failure that looks exactly like a
            # part with no published specs is the kind of thing nobody notices until
            # the board is wrong.
            log.warning("normalisation failed for %s: %s", candidate.mpn, error)
            fields, provenance = {}, {}

    _discard_non_regulator_fields(fields, provenance, candidate.category)

    # Facts only fill a blank from this run. A live listing is the buying truth, even
    # when a durable fact is newer-looking or appears more complete.
    for field, (value, source) in dossier_fields.items():
        if field in {"package", "theta_ja"} or field in fields:
            continue
        fields[field] = value
        provenance[field] = dossier.provenance(source)
    if thermal_fact is None and "theta_ja" in dossier_fields:
        provenance["theta_ja"] = dossier.provenance(dossier_fields["theta_ja"][1])
    if not candidate.package and "package" in dossier_fields:
        provenance["package"] = dossier.provenance(dossier_fields["package"][1])
    _discard_non_regulator_fields(fields, provenance, candidate.category)

    try:
        extra = await enrichment
    except Exception as error:  # noqa: BLE001
        # A part that has been found and typed is usable. Losing its datasheet link or
        # lifecycle is a smaller loss than losing the board — and a run that dies here
        # shows the user "ERROR" with no explanation of what broke.
        log.warning("enrichment failed for %s: %s", candidate.mpn, error)
        extra = search.Enrichment()

    try:
        current_stock = await live_stock
    except Exception as error:  # noqa: BLE001
        # A part that has been found and typed is usable. Live inventory is more current
        # than the search index, but it cannot be allowed to take down that useful run.
        log.warning("live stock lookup failed for %s: %s", candidate.mpn, error)
        current_stock = None

    # Only a bus master has a GPIO budget worth counting, and the symbol lookup is a
    # whole extra call — so it is fetched for controllers and nothing else.
    if fields.get("role") == "master" and not fields.get("pins_available"):
        pins = await search.pinout(candidate.lcsc)
        available = gpio_count(pins)
        if available:
            fields["pins_available"] = available
            provenance["pins_available"] = "EasyEDA symbol pin names"

    return PartSpec(
        **{
            **_from_payload(
                candidate,
                extra.datasheet,
                extra.lifecycle,
                current_stock if current_stock is not None else candidate.stock,
                dossier_fields,
            ),
            **fields,
            "theta_ja": (
                thermal_fact.theta_ja
                if thermal_fact is not None
                else dossier_fields.get("theta_ja", (None, None))[0]
            ),
            "theta_ja_source_line": (
                thermal_fact.source_line
                if thermal_fact is not None
                else dossier_fields.get("theta_ja", (None, None))[1]
            ),
            "provenance": provenance,
        }
    )


async def normalize_all(
    candidates: list[Candidate], *, dossier_lookup: DossierLookup | None = None
) -> list[PartSpec]:
    """Concurrent. The rate limiter is shared and enforces the quota across all of them."""
    return list(await asyncio.gather(*(normalize(c, dossier_lookup=dossier_lookup) for c in candidates)))


# ── cache ─────────────────────────────────────────────────────────────────────


def _cache_path(mpn: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in mpn)
    return CACHE_DIR / f"{safe}.json"


def _prompt_version() -> str:
    """Fingerprint of the instructions that produced a cached parse.

    A cache keyed only by MPN outlives the prompt that filled it, so tightening an
    instruction silently changes nothing until somebody remembers to delete the
    directory. Keying on the prompt too means a reworded rule re-parses on its own.
    """
    return hashlib.sha256(SYSTEM.encode()).hexdigest()[:12]


def _load(mpn: str) -> tuple[dict, dict] | None:
    path = _cache_path(mpn)
    if not path.exists():
        return None
    try:
        stored = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    if stored.get("prompt") != _prompt_version():
        return None
    fields = stored.get("fields", {})
    if isinstance(fields.get("interfaces"), list):
        fields["interfaces"] = tuple(fields["interfaces"])
    return fields, stored.get("provenance", {})


def _save(mpn: str, fields: Mapping[str, Any], provenance: Mapping[str, str]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    serialisable = {k: (list(v) if isinstance(v, tuple) else v) for k, v in fields.items()}
    _cache_path(mpn).write_text(
        json.dumps(
            {
                "mpn": mpn,
                "prompt": _prompt_version(),
                "fields": serialisable,
                "provenance": dict(provenance),
            },
            indent=1,
        )
    )
