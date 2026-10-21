"""Test wiring.

## The default suite never touches the network

Once `select` and `apply` search a distributor and call a model, driving the graph in a
test means several seconds of network per run — and a suite slow enough to skip is a
suite that stops catching things.

So sourcing is served from a fixed catalogue by default. That is not a loss of
coverage: the graph tests exist to check the *wire and the loop* — sequence numbers,
edge patches, the interrupt round-trip, which conflict fires and in what order — and
none of that is about JLCPCB's inventory. The live path has its own tests in
`test_parts.py`, gated behind `CONTINUITY_LIVE=1`.

Set `CONTINUITY_LIVE=1` to run everything against the real services instead.
"""

from __future__ import annotations

import os

import pytest

from continuity import llm, reviewer
from continuity.graph import catalogue, sourcing
from continuity.planner import plan as planner
from continuity.parts import datasheet, normalize
from continuity.parts.search import Candidate

LIVE = os.environ.get("CONTINUITY_LIVE") == "1"


@pytest.fixture(autouse=True)
def isolated_part_caches(monkeypatch, tmp_path):
    """Keep normalisation and datasheet facts out of a developer's real cache."""
    monkeypatch.setattr(normalize, "CACHE_DIR", tmp_path / "normalized")
    monkeypatch.setattr(datasheet, "CACHE_DIR", tmp_path / "datasheets")


def _as_candidate(part) -> Candidate:
    """Wrap a catalogue `PartSpec` so it looks like a raw search hit."""
    return Candidate(
        lcsc=f"C{abs(hash(part.mpn)) % 10_000_000}",
        mpn=part.mpn,
        manufacturer=part.manufacturer,
        description=part.description,
        package=part.package,
        category=part.category,
        subcategory=part.category,
        stock=part.stock or 0,
        unit_price=part.unit_price,
        library_type="basic",
        specs=dict(part.raw),
    )


_BY_QUERY = {
    "3.3V LDO regulator": "regulator",
    "ESP32 module": "mcu",
    "temperature humidity sensor": "sensor",
    "OLED display": "display",
}


@pytest.fixture(autouse=True)
def offline_sourcing(monkeypatch, request):
    """Serve slot candidates from the catalogue instead of the distributor."""
    if LIVE or request.node.get_closest_marker("live"):
        return

    async def find(query: str, *, constraint=None, **_context):
        slot = _BY_QUERY.get(query)
        if slot is None:
            # A constraint rewrites the query into something like "3.3V buck converter".
            # Anything that is not a plain LDO search is the regulator being replaced.
            slot = "regulator" if "regulator" in query or "converter" in query else None
        if slot is None:
            return []

        parts = catalogue.CATALOGUE[slot]
        if constraint and constraint.get("topology") == "buck":
            parts = [p for p in parts if p.is_switching]
        return [_as_candidate(p) for p in parts]

    async def choose(candidate: Candidate):
        for options in catalogue.CATALOGUE.values():
            for part in options:
                if part.mpn == candidate.mpn:
                    return part
        raise AssertionError(f"no catalogue part for {candidate.mpn}")

    # Every model-backed step has a deterministic fallback, and offline we take it.
    # Reporting the key as absent is what selects those paths — one switch rather than
    # one patch per integration, so a new model call cannot quietly reach the network
    # from the default suite.
    monkeypatch.setattr(llm, "available", lambda: False)
    monkeypatch.setattr(planner.llm, "available", lambda: False)
    monkeypatch.setattr(reviewer.llm, "available", lambda: False)
    monkeypatch.setattr(sourcing, "find", find)
    monkeypatch.setattr(sourcing, "choose", choose)
