"""Finding real parts for a slot, and re-finding them under a repair's constraint.

Replaces `catalogue.py`. Same shape from the graph's point of view — ask for a slot's
candidates, take the first, take the next one on repair — but the parts are now fetched
from JLCPCB and typed by the normaliser.

## Only the chosen part is normalised

A search returns six or eight candidates and normalising all of them would be six or
eight LLM calls per slot. It is also unnecessary: the *alternatives* list shown in the
repair drawer needs an MPN, a manufacturer, a price, stock and a lead time, and every
one of those is already typed in the raw payload. Only the part the engine is about to
check needs its free-text specs parsed.

So the rule is: normalise what will be *validated*, not what will be *displayed*. One
call per placement, one more per repair.

## Constraints go into the query, not into a filter afterwards

A repair that says "this must be a buck converter supplying 1 A" re-searches with that
pushed down, rather than paging through linear regulators and discarding them. The
exception is a plain `swap`, which just advances to the next candidate already in hand —
no new search, no new latency.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import re
from typing import Any, Callable, Iterator, Mapping, Sequence

from .. import interpret
from ..engine import packages
from ..engine.models import Alternative, PartSpec
from ..parts import categories, datasheet, normalize, payload
from ..parts.search import Candidate, SpecFilter, search

CANDIDATES_PER_SLOT = 6
"""Enough that a repair has somewhere to go without a fresh search."""

MIN_CANDIDATES = 3
"""A shortlist needs this many viable parts before it can repair a failed choice."""

_rescue_narrator: ContextVar[Callable[[str], None] | None] = ContextVar(
    "rescue_narrator", default=None
)


@contextmanager
def narrate_rescue(callback: Callable[[str], None]) -> Iterator[None]:
    """Report the optional second distributor request without coupling search to SSE."""
    token = _rescue_narrator.set(callback)
    try:
        yield
    finally:
        _rescue_narrator.reset(token)


class SearchResults(list[Candidate]):
    """A candidate list that records whether its one allowed rescue was used."""

    def __init__(
        self,
        candidates: Sequence[Candidate],
        *,
        rescue_subcategory: str | None = None,
        demoted: Sequence[str] = (),
    ) -> None:
        super().__init__(candidates)
        self.rescue_subcategory = rescue_subcategory
        self.demoted = tuple(demoted)
        """MPNs moved to the back as the wrong *kind* of part. Narrated, never silent."""


DISTRIBUTOR_TOPOLOGY = {"buck": "Buck", "boost": "Boost", "buck-boost": "Buck-Boost"}
"""Our topology vocabulary → JLCPCB's `Topology` parameter values.

`change_topology: boost` used to be pushed into the *query text* as "3.3V boost
converter". Measured live, that returns `XL1509-5.0E1`, `TPS5430DDAR`, `XL1509-ADJE1` —
every one a buck. JLCPCB's text search does not weight the word, so the repair got the
same part back, failed identically, and spent every attempt until the fence stopped it.

Filtering on the parameter instead returns `TPS61040DBVR`, `MT3608`, `SX1308` — actual
boost parts, one of which the reviewer had already named by hand. The lesson is the one
`constraint.mpn` taught: when the constraint is a *property*, say so in a filter; a
distributor's free-text search is not a specification language.

Absent from this map on purpose: `ldo` and `linear` are a different JLCPCB category
rather than a Topology value, and `switching` is a family, not a parameter value.
"""


LOCAL_ONLY = ("vin_min", "vout", "rated_to", "rated_from", "category")
"""Constraints the distributor's filters cannot express, applied by `viable()` instead.

`vin_min`, `vout`, and `rated_to` compare against the *ends of a range* held in one
payload string, which a spec filter reads as the range's minimum. `vout` was in
`CONSTRAINT_FIELDS` and being emitted by the reviewer all along — it used to prepend a
voltage to the query text, and when the topology filter replaced that it became a
constraint the system accepted and then ignored.

`category` is here because JLCPCB publishes no filter at the category level at all: the
only lever is `subcategory_name`, which is finer than our vocabulary and matches fuzzily.
`parts.categories` records the probe. Every row states its own category, so filtering
here costs nothing beyond a deeper pool to filter from.
"""

DEEP_POOL = 40
"""How many candidates to fetch when a constraint is applied locally.

Filtering the usual six is useless when all six are wrong. Measured on the 48 V board:
at six, every buck returned is a 40 V part and `vin_min` keeps nothing; at forty there
are six provably capable ones — `XL7005A` at 5–80 V, `TX4137` at 5.5–60 V. Depth is a
parameter on the same single request, so the pool costs a bigger response and not a
second round trip.
"""


def merge_constraints(
    slot: Mapping[str, Any] | None, repair: Mapping[str, Any] | None
) -> dict[str, Any]:
    """What the slot *is*, plus what the repair additionally demands.

    A repair says what must change; it does not restate everything that was already
    true. On the 48 V board the reviewer chose `swap` with `vin_min: 48` and no topology
    — correctly, because it was not changing the kind of part — and that alone dropped
    the slot's `topology: buck`. The re-search fell back to raw text, returned a screw
    terminal as its only survivor, and the run escalated saying nothing matched.

    The repair wins on conflict, so `change_topology: boost` still overrides a slot
    planned as a buck.
    """
    return {**(slot or {}), **(repair or {})}


def pool_size(constraint: Mapping[str, Any]) -> int:
    """How many candidates to fetch. Wider when something must be filtered locally."""
    local = any(constraint.get(key) is not None for key in LOCAL_ONLY)
    return DEEP_POOL if local else CANDIDATES_PER_SLOT


async def find(
    query: str,
    *,
    constraint: Mapping[str, Any] | None = None,
    purpose: str | None = None,
    board: str | None = None,
) -> list[Candidate]:
    """Candidates for a slot, best-first. Applies constraint pushdown when given one.

    `purpose` is what the slot is *for* — its label. Supplying it enables one last filter
    that asks whether each candidate is the right **kind** of part, which neither the text
    query nor the category filter can decide; see `interpret.unfit_candidates`. Omitting it
    keeps the previous behaviour exactly, which is what every offline test relies on.
    """
    constraint = constraint or {}
    refined, filters, package = _push_down(query, constraint)

    hits = await search(
        refined,
        spec_filters=filters or None,
        package=package,
        limit=pool_size(constraint),
    )
    found = viable(hits, constraint)
    subcategory = _rescue_subcategory(query, constraint)
    rescue_subcategory = None
    # Thin *or* aimed at the wrong shelf. Six crystals is not a thin shortlist for a
    # real-time-clock slot, it is a full one pointing at the wrong kind of part, and only
    # the second test catches that.
    if (len(found) < MIN_CANDIDATES or _misses_defining_shelf(found, constraint)) and subcategory:
        rescue_subcategory = subcategory
        narrator = _rescue_narrator.get()
        if narrator is not None:
            narrator(subcategory)
        rescue = await search(
            "",
            spec_filters=filters or None,
            package=package,
            subcategory_name=subcategory,
            limit=pool_size(constraint),
        )
        known_mpns = {candidate.mpn for candidate in found}
        for candidate in viable(rescue, constraint):
            if candidate.mpn not in known_mpns:
                found.append(candidate)
                known_mpns.add(candidate.mpn)
        # Re-applied to the *merged* list, which is the only place it can decide anything.
        # Inside `viable` it runs twice over two lists that never meet: once on the hits
        # that triggered the rescue, once on the rescue itself. Each was compared only
        # against its own members, so the rescue's real drivers were appended *behind* the
        # parent-shelf parts and the shortlist slice kept the wrong ones anyway. Measured
        # on a motor brief that still placed a 2-input AND gate after the shelves were
        # declared defining.
        found = _on_defining_shelf(found, constraint)
    shortlist = found[:CANDIDATES_PER_SLOT]
    demoted: tuple[str, ...] = ()
    # A single-candidate shortlist cannot be reordered, so a judgement about it can only
    # produce a misleading line on screen — measured: the classifier called a genuine buck
    # regulator "not a buck regulator" because its fixed output was the wrong voltage.
    if purpose and len(shortlist) > 1:
        shortlist, demoted = rank_by_fitness(
            shortlist,
            await interpret.unfit_candidates(
                purpose=purpose, query=query, candidates=shortlist, board=board
            ),
        )

    return SearchResults(
        shortlist, rescue_subcategory=rescue_subcategory, demoted=demoted
    )


def rank_by_fitness(
    shortlist: Sequence[Candidate], unfit: set[str]
) -> tuple[list[Candidate], tuple[str, ...]]:
    """Move the candidates judged the wrong *kind* of part to the back. Remove nothing.

    Reordering rather than filtering, and the difference matters. This judgement comes
    from a model and it is measurably imperfect — probed against live shortlists on
    13 Aug it correctly rejected six crystal oscillators offered for a real-time-clock
    slot, and also rejected a genuine buck regulator for having the wrong output voltage,
    which is a number the engine checks and it should not have been reading.

    A wrong *removal* costs the board a part it needed and cannot be undone. A wrong
    *demotion* costs a position in a list the repair loop walks anyway. So the upside is
    kept — when the judgement is right, the right kind of part is chosen first — and the
    downside is bounded to ordering. It is also why there is no "never empty the list"
    special case any more: nothing is ever taken away.

    Stable, so the distributor's own ranking still decides within each group.
    """
    suspect = [c for c in shortlist if c.mpn in unfit]
    if not suspect:
        return list(shortlist), ()
    trusted = [c for c in shortlist if c.mpn not in unfit]
    return trusted + suspect, tuple(c.mpn for c in suspect)


def _rescue_subcategory(query: str, constraint: Mapping[str, Any]) -> str | None:
    """The single most relevant verified subcategory, when a rescue is permitted.

    **A rescue that matched nothing is refused**, unless the category says its shelves are
    interchangeable. Falling back to declaration order used to be unconditional, and the
    docstring called it deliberate — for `sensor` it is, because "environmental sensor"
    shares no word with any shelf and every shelf is still a sensor. For a category whose
    shelves are mutually exclusive it is simply a wrong answer, and it produced two:
    a `CR1220` coin cell for a battery-holder slot, and an ESP32 for a GPS slot.

    Refusing costs a thin shortlist, which the run already narrates, and at worst an
    unfilled slot — which is visibly incomplete rather than invisibly wrong.

    Synonyms are applied to the query first, so `GPS` can find the shelf JLCPCB calls
    `GNSS Modules`. That is the difference between refusing this rescue and getting it
    right.
    """
    if constraint.get("mpn"):
        return None
    category = categories.CATEGORIES.get(str(constraint.get("category") or "").strip().lower())
    if category is None or not category.subcategories:
        return None

    words = {
        categories.SHELF_SYNONYMS.get(word, word)
        for word in re.findall(r"[a-z]+", query.lower())
    } - {"sensor", "sensors"}

    def overlap(name: str) -> int:
        return len(words & set(re.findall(r"[a-z]+", name.lower())))

    best = max(category.subcategories, key=overlap)
    if overlap(best) > 0 or category.rescue_unmatched:
        return best
    # A category whose shelves are its *definition* may always widen to them: there is
    # nowhere else a part of that kind can live, so a query that matched none of them was
    # a bad query rather than a signal to give up. This is what rescues a slot labelled
    # "Real-Time Clock" whose query read "RTC crystal oscillator" — the words name a
    # crystal, the distributor duly returns crystals, and the shelf is the only thing that
    # still knows what the slot was for.
    return best if category.defining_shelves else None


def viable(candidates: Sequence[Candidate], constraint: Mapping[str, Any]) -> list[Candidate]:
    """Drop what provably fails, then put the checkable ones first.

    Everything here reads the *raw payload* — no model, no extra request — and the values
    it reads never reach a rule. They decide which candidates are worth normalising; the
    engine still computes verdicts from the normalised `PartSpec` with its provenance. A
    wrong read costs a worse shortlist, never a wrong verdict.

    Two jobs the distributor's own filters cannot do:

    **Reject on a ceiling.** `Voltage - Supply` holds a whole range in one string, and a
    spec filter over it matches the *minimum* — `Voltage - Supply >= 48V` returns nothing,
    because no buck has a minimum above 48 V. So a 48 V board could not ask for a part
    rated to 48 V, and every candidate came back a 40 V one.

    **Prefer what can be decided.** A boost search returned three valid parts; the first
    stated no output minimum, so the board's central claim ended up *unchecked* while the
    other two were provable. The distributor does not rank by how completely it
    documented a part.
    """
    survivors = _on_defining_shelf([c for c in candidates if _survives(c, constraint)], constraint)
    # Stable, so the distributor's own ordering still decides between equals.
    return sorted(survivors, key=lambda c: not payload.states_output_range(c.specs))


def _misses_defining_shelf(
    candidates: Sequence[Candidate], constraint: Mapping[str, Any]
) -> bool:
    """True when this category is defined by its shelves and nothing found is on one."""
    category = categories.CATEGORIES.get(str(constraint.get("category") or "").strip().lower())
    if category is None or not category.defining_shelves:
        return False
    shelves = {name.casefold() for name in category.subcategories}
    return not any((c.subcategory or "").strip().casefold() in shelves for c in candidates)


def _on_defining_shelf(
    candidates: list[Candidate], constraint: Mapping[str, Any]
) -> list[Candidate]:
    """Keep only the shelves that *define* the category, while any of them are present.

    The top-level category filter cannot separate a battery charger from a DC-DC
    converter — JLCPCB files both under `Power Management (PMIC)`, and our own vocabulary
    called them the same kind of part until 13 Aug. The difference is one level down, on
    the subcategory every candidate already carries.

    **Never empties the list.** If nothing is on the defining shelf this returns everything
    unchanged, so a vocabulary that is wrong about where a part lives costs a worse
    ordering rather than an empty slot — the same direction `_survives` takes with a spec
    it cannot read.
    """
    category = categories.CATEGORIES.get(str(constraint.get("category") or "").strip().lower())
    if category is None or not category.defining_shelves:
        return candidates

    shelves = {name.casefold() for name in category.subcategories}
    on_shelf = [c for c in candidates if (c.subcategory or "").strip().casefold() in shelves]
    return on_shelf or candidates


def _survives(candidate: Candidate, constraint: Mapping[str, Any]) -> bool:
    """Only a *provable* violation removes a candidate.

    `None` means the payload did not say, and that keeps the part. Turning "cannot tell"
    into "no" is the one direction this system may not guess in — the engine will report
    it as unchecked, which is the honest outcome.
    """
    if categories.satisfies(constraint.get("category"), candidate.category) is False:
        return False

    checks = (
        (constraint.get("vin_min"), payload.accepts_input),
        (constraint.get("vout"), payload.can_output),
        (constraint.get("rated_to"), payload.rated_to),
        (constraint.get("rated_from"), payload.rated_from),
    )
    return all(
        check(candidate.specs, float(value)) is not False
        for value, check in checks
        if isinstance(value, (int, float))
    )


def _push_down(
    query: str, constraint: Mapping[str, Any]
) -> tuple[str, list[SpecFilter], str | None]:
    """Turn a repair's constraint into search arguments.

    Only constraints with a *verified* parameter mapping are pushed. Measured 9 Aug: an
    unrecognised filter name is **silently ignored** by the server, not rejected —
    `SpecFilter("Voltage - Input (Max)", ">=", "48V")` returns the same unfiltered hits
    as no filter at all. So a filter on a plausible-sounding parameter looks like it
    worked and does nothing, and a name has to be checked against a live payload before
    being trusted here.

    Constraints the server cannot express — anything comparing against the *top* of a
    range — are left in the constraint for `viable()` to apply locally.
    """
    filters: list[SpecFilter] = []
    refined = query

    # A named part needs no search at all; `apply` resolves it against the candidates
    # already in hand. Reaching here with one means it was not among them.
    if constraint.get("mpn"):
        return query, [], None

    topology = constraint.get("topology")
    # Topology is a property of a *regulator*, and pushing it rewrites the query text —
    # so it may only be pushed for a slot that is one. The planner stamps a topology on
    # whatever sources a rail, because the prompt asks it to compare rail voltages, and a
    # PoE powered-device controller sourcing a 5 V rail from 48 V is duly labelled "buck".
    # The rewrite then threw away the word "PoE" and searched for a dc-dc converter, which
    # is a fine answer to a question the board never asked: the run put an 80 V buck in a
    # PoE controller slot and every electrical check passed it. Measured 13 Aug — the same
    # slot searched on its own text returns six genuine PoE controllers.
    kind = str(constraint.get("category") or "").strip().lower()
    if isinstance(topology, str) and topology and kind in ("", "regulator"):
        mapped = DISTRIBUTOR_TOPOLOGY.get(topology)
        if mapped is None:
            # Linear parts are a different JLCPCB *category*, not a Topology value.
            refined = "LDO regulator"
        else:
            refined = "dc-dc converter"
            filters.append(SpecFilter("Topology", "=", mapped))

    current = constraint.get("i_out_min")
    if isinstance(current, (int, float)) and current > 0:
        filters.append(SpecFilter("Output Current", ">=", f"{current:g}A"))

    package = constraint.get("package")
    return refined, filters, package if isinstance(package, str) else None


async def choose(candidate: Candidate) -> PartSpec:
    """Normalise one candidate into something the engine can check."""
    missing_theta_ja = (
        datasheet._load(candidate.mpn) is None and packages.theta_ja(candidate.package) is None
    )
    return await normalize.normalize(candidate, fetch_missing_theta_ja=missing_theta_ja)


def alternatives(
    considered: Sequence[Candidate], chosen: Candidate, reason: str
) -> list[Alternative]:
    """The legal candidate set, for the repair drawer.

    Built from raw payload fields — no normalisation, because nothing here is validated
    against a rule. Exactly one entry is marked recommended: the one being applied.
    """
    rows: list[Alternative] = []
    for candidate in list(considered)[:3]:
        is_chosen = candidate.mpn == chosen.mpn
        rows.append(
            Alternative(
                mpn=candidate.mpn,
                manufacturer=candidate.manufacturer,
                reason=reason if is_chosen else _summarise(candidate),
                recommended=is_chosen,
                unit_price=candidate.unit_price,
                currency="USD",
                stock=candidate.stock,
                lead_time_days=0,
                datasheet=candidate.product_url,
            )
        )
    if rows and not any(row.recommended for row in rows):
        rows.insert(
            0,
            Alternative(
                mpn=chosen.mpn,
                manufacturer=chosen.manufacturer,
                reason=reason,
                recommended=True,
                unit_price=chosen.unit_price,
                currency="USD",
                stock=chosen.stock,
                lead_time_days=0,
                datasheet=chosen.product_url,
            ),
        )
    return rows


def _summarise(candidate: Candidate) -> str:
    price = f"${candidate.unit_price:.2f}" if candidate.unit_price else "price unknown"
    return f"{price}, {candidate.stock:,} in stock"
