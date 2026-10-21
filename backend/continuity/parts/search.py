"""Searching JLCPCB for candidates, and pushing constraints into the query.

A `Candidate` is a raw distributor result — not yet a `PartSpec`. The engine never
sees one of these; `normalize` turns it into a typed spec first, and only then can a
rule look at it.

## Why the stock floor is not pushed down

`jlc_search` takes `min_stock`, and the local index only holds parts with stock >= 10.
Pushing `requirements.min_stock` into the query would mean a part that cannot cover the
production run is never *returned*, so R6 would never fail and the sourcing conflict —
the whole sold-out beat — could not happen.

That is the wrong shape. The search should find what exists; the engine decides whether
it is good enough, and says so with evidence. So the floor stays a requirement, checked
by R6 after the fact, and the query asks only for parts that are buyable at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit

from . import mcp

DEFAULT_LIMIT = 8
"""Enough for a reviewer to have real alternatives, few enough to normalise cheaply."""


# Search engines observed across the recorded cse_search fixtures, 11 Aug.
_SEARCH_ENGINE_HOSTS = ("google.", "bing.", "duckduckgo.", "baidu.")


def _document_url(value: Any) -> str | None:
    """Return a checkable document URL, rejecting obvious search pages."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not host:
        return None

    host = host.lower()
    if any(host.startswith(search_host) or f".{search_host}" in host for search_host in _SEARCH_ENGINE_HOSTS):
        return None
    if parsed.path == "/search":
        return None
    if any(name in {"q", "query"} for name, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        return None
    return value


@dataclass(frozen=True)
class Candidate:
    """One raw search hit, before normalisation."""

    lcsc: str
    mpn: str
    manufacturer: str
    description: str
    package: str | None
    category: str
    subcategory: str
    stock: int | None
    unit_price: float | None
    library_type: str | None
    specs: Mapping[str, str] = field(default_factory=dict)

    @property
    def product_url(self) -> str:
        return f"https://jlcpcb.com/partdetail/{self.lcsc}"


@dataclass(frozen=True)
class SpecFilter:
    """A parametric constraint pushed into the query rather than filtered afterwards.

    This is what makes a repair re-search cheap: rather than fetching a page of
    regulators and discarding the ones that cannot supply 1 A, ask for the ones that
    can. Cheaper, faster, and it keeps normalisation off the hot path.
    """

    name: str
    op: str
    value: str | float

    def as_dict(self) -> dict[str, Any]:
        # The server validates `value` as a string and rejects a bare number, so a
        # numeric filter has to be stringified here rather than at the call site.
        # Units belong in the string too: "1A" and "1" are not the same query.
        return {"name": self.name, "op": self.op, "value": str(self.value)}


def _candidate(row: Mapping[str, Any]) -> Candidate:
    return Candidate(
        lcsc=str(row.get("lcsc", "")),
        mpn=str(row.get("model", "")),
        manufacturer=str(row.get("manufacturer", "")),
        description=str(row.get("description", "")),
        package=row.get("package"),
        category=str(row.get("category", "")),
        subcategory=str(row.get("subcategory", "")),
        # Never 0 by default: 0 means "out of stock", which R6 fails on. A part whose
        # stock was simply not reported would raise a conflict that is not real.
        stock=int(row["stock"]) if row.get("stock") is not None else None,
        unit_price=row.get("price"),
        library_type=row.get("library_type"),
        specs=dict(row.get("specs") or {}),
    )


async def search(
    query: str,
    *,
    package: str | None = None,
    packages: list[str] | None = None,
    spec_filters: list[SpecFilter] | None = None,
    subcategory_name: str | None = None,
    sort_by: str = "stock",
    limit: int = DEFAULT_LIMIT,
) -> list[Candidate]:
    """Search JLCPCB. Returns candidates best-first by the chosen sort.

    Falls back when a descriptive query finds nothing, which happens far more often
    than it looks. The server runs a *smart parser* over the query: it detects a
    component type, pins the search to that subcategory, and matches whatever words
    are left against records inside it. So "wifi bluetooth module" becomes
    `subcategory=Bluetooth Modules` + text `"wifi"` — and no Bluetooth module says
    "wifi", so the answer is zero results.

    Every word the planner adds for precision can therefore *cost* a match. Retrying
    with the component type the server itself detected uses its own parse rather than
    second-guessing it, and a slot that silently finds nothing is much worse than one
    that finds loose matches the engine can reject on the specs.
    """
    rows, payload = await _search_once(
        query, package=package, packages=packages, spec_filters=spec_filters,
        subcategory_name=subcategory_name, sort_by=sort_by, limit=limit,
    )
    if rows:
        return rows

    for retry in _fallback_queries(query, payload):
        rows, _ = await _search_once(
            retry, package=package, packages=packages, spec_filters=spec_filters,
            subcategory_name=subcategory_name, sort_by=sort_by, limit=limit,
        )
        if rows:
            return rows
    return []


def _fallback_queries(query: str, payload: Any) -> list[str]:
    """Progressively less specific queries, most promising first.

    The server's own `parsed.detected.component_type` comes first: it is the server
    telling us what it thought we meant, which beats any guess made from this side.
    """
    attempts: list[str] = []

    if isinstance(payload, Mapping):
        detected = (payload.get("parsed") or {}).get("detected") or {}
        for key in ("component_type", "subcategory"):
            value = detected.get(key)
            if isinstance(value, str) and value and value not in attempts:
                attempts.append(value)

    words = query.split()
    if len(words) > 2:
        attempts.append(" ".join(words[:2]))
    if len(words) > 1:
        attempts.append(words[0])

    return [a for a in attempts if a.strip() and a.strip() != query.strip()]


async def _search_once(
    query: str,
    *,
    package: str | None,
    packages: list[str] | None,
    spec_filters: list[SpecFilter] | None,
    subcategory_name: str | None,
    sort_by: str,
    limit: int,
) -> tuple[list[Candidate], Any]:
    payload = await mcp.call_tool(
        "jlc_search",
        {
            "query": query,
            "package": package,
            "packages": packages,
            "spec_filters": [f.as_dict() for f in spec_filters] if spec_filters else None,
            "subcategory_name": subcategory_name,
            "sort_by": sort_by,
            "limit": limit,
        },
    )

    if isinstance(payload, str):
        return [], None  # the tool answered in prose, which means nothing usable

    rows = payload.get("results") if isinstance(payload, Mapping) else payload
    return [_candidate(r) for r in (rows or []) if r.get("model")], payload


async def get_part(*, lcsc: str | None = None, mpn: str | None = None) -> Mapping[str, Any] | None:
    """Full detail for one part, by LCSC code or MPN.

    Returns the same field set as a search hit, wrapped in `results` — notably *not* a
    datasheet, which JLCPCB does not publish through this API at all. See
    `datasheet_for`.

    Takes `lcsc` or `mpn`; the tool has no generic identifier argument.
    """
    if not lcsc and not mpn:
        return None
    payload = await mcp.call_tool("jlc_get_part", {"lcsc": lcsc, "mpn": mpn})
    return payload if isinstance(payload, Mapping) else None


async def datasheet_for(mpn: str) -> str | None:
    """A checkable URL for a part, or None.

    JLCPCB's payload carries no datasheet link — neither `jlc_search` nor
    `jlc_get_part` returns one — so this asks the ECAD index instead.

    This matters more than it looks. Every verdict cites its evidence with a `source`,
    and a source of `null` is a row a judge cannot check. Where no datasheet exists,
    the caller falls back to the distributor product page, which is honest in a way a
    fabricated datasheet URL would not be: the values really did come from that page.
    """
    try:
        payload = await mcp.call_tool("cse_search", {"query": mpn})
    except mcp.ToolError:
        # `cse_search` is the slowest tool we call and the likeliest to time out. It
        # supplies a *link*, and a missing link is a missing link — it must never take
        # down a run that has already found and typed the part.
        return None
    if not isinstance(payload, Mapping):
        return None
    for row in payload.get("results") or []:
        if datasheet := _document_url(row.get("datasheet_url")):
            return datasheet
    return None


@dataclass(frozen=True)
class Enrichment:
    """What a second source knows that JLCPCB's payload does not."""

    datasheet: str | None = None
    lifecycle: str | None = None


_LIFECYCLE = {
    "active": "active",
    # Observed across the recorded Mouser fixtures, 11 Aug.
    "new product": "active",
    "new at mouser": "active",
    "not recommended for new designs": "nrnd",
    "nrnd": "nrnd",
    "obsolete": "obsolete",
    "end of life": "obsolete",
    "discontinued": "obsolete",
}


async def enrich(mpn: str) -> Enrichment:
    """Lifecycle and datasheet for a part, from whoever actually publishes them.

    JLCPCB gives neither. Mouser gives both in one call, so it is tried first; the ECAD
    index is the datasheet fallback when Mouser does not carry the part.

    Lifecycle matters more than it looks. R6 warns on `nrnd` and `obsolete`, so a part
    whose status is unknown must stay `unknown` — asserting `active` would silence that
    warning for every part on every board and there would be nothing on screen to say so.
    """
    datasheet: str | None = None
    lifecycle: str | None = None

    try:
        payload = await mcp.call_tool("mouser_get_part", {"part_number": mpn})
    except Exception:  # noqa: BLE001 - enrichment is decoration; nothing here is fatal
        payload = None

    if isinstance(payload, Mapping):
        for row in payload.get("results") or []:
            if row.get("mfr_part_number", "").upper() != mpn.upper():
                continue
            datasheet = datasheet or _document_url(row.get("datasheet_url"))
            stated = str(row.get("lifecycle") or "").strip().lower()
            lifecycle = lifecycle or _LIFECYCLE.get(stated)
            break

    if datasheet is None:
        try:
            datasheet = await datasheet_for(mpn)
        except Exception:  # noqa: BLE001
            datasheet = None

    return Enrichment(datasheet=datasheet, lifecycle=lifecycle)


async def live_stock(mpn: str) -> int | None:
    """Current JLCPCB stock for exactly this MPN, or None when it cannot be established.

    A found and typed part is still usable when this slow live lookup fails, so it must
    never take down a run that already has its answer. The query is fuzzy; only an
    exact, case-insensitive match on JLCPCB's `model` field is safe to use.
    """
    try:
        payload = await mcp.call_tool("jlc_stock_check", {"query": mpn, "limit": 5})
    except Exception:  # noqa: BLE001 - live inventory is useful only when available
        return None

    if not isinstance(payload, Mapping):
        return None
    rows = payload.get("results")
    if not isinstance(rows, list):
        return None

    for row in rows:
        if not isinstance(row, Mapping):
            continue
        model = row.get("model")
        if not isinstance(model, str) or model.upper() != mpn.upper():
            continue
        stock = row.get("stock")
        return stock if isinstance(stock, int) and not isinstance(stock, bool) else None
    return None


async def pinout(lcsc: str) -> tuple[str, ...]:
    """Pin names for a part, from its EasyEDA symbol. Empty when none is published.

    JLCPCB's parametric data has no pin or GPIO count anywhere, which left R3 unable to
    evaluate on any board. The symbol has both.
    """
    try:
        payload = await mcp.call_tool("jlc_get_pinout", {"lcsc": lcsc})
    except Exception:  # noqa: BLE001 - a missing pin count makes R3 report unchecked
        return ()
    if not isinstance(payload, Mapping):
        return ()
    return tuple(
        str(pin.get("name", "")) for pin in (payload.get("pins") or []) if pin.get("name")
    )
