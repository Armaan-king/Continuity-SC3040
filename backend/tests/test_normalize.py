"""Normalisation, and the fence around what a model may return.

This is where "the model fills declared fields, it never adds one" stops being a
sentence in a design doc and becomes something that fails a test.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from continuity.engine import rules
from continuity.engine.models import Requirements
from continuity.graph.sourcing import choose as choose_candidate
from continuity.graph import sourcing
from continuity.parts import datasheet, mcp, normalize
from continuity.parts.search import Candidate
from tests import parts
from tests.boards import usb_board

SPECS = {
    "Voltage - Supply": "1.08V~3.6V",
    "Current - Supply": "400nA",
    "Interface": "I2C",
    "Operating Temperature": "-40℃~+125℃",
}

CANDIDATE = Candidate(
    lcsc="C2909890",
    mpn="SHT40-AD1B-R2",
    manufacturer="Sensirion",
    description="temperature and humidity sensor",
    package="DFN-4-EP(1.5x1.5)",
    category="Sensors",
    subcategory="Temperature and Humidity Sensor",
    stock=20760,
    unit_price=2.0122,
    library_type="extended",
    specs=SPECS,
)


def validate(raw):
    return normalize.validate(raw, SPECS)


@pytest.fixture(autouse=True)
def no_live_stock_lookup(monkeypatch):
    """Keep existing normalisation tests offline unless they opt into this lookup."""
    actual_live_stock = normalize.search.live_stock

    async def unavailable(_mpn):
        return None

    monkeypatch.setattr(normalize.search, "live_stock", unavailable)
    return actual_live_stock


# ── what it accepts ───────────────────────────────────────────────────────────


def test_a_well_formed_reply_passes_through():
    fields, provenance = validate(
        {
            "vmin": 1.08,
            "vmax": 3.6,
            "i_typ": 0.0000004,
            "interfaces": ["i2c"],
            "role": "peripheral",
            "provenance": {"vmin": "Voltage - Supply", "i_typ": "Current - Supply"},
        }
    )

    assert fields["vmin"] == 1.08
    assert fields["interfaces"] == ("I2C",)
    assert fields["role"] == "peripheral"
    assert provenance["i_typ"] == "Current - Supply"


def test_integers_are_accepted_where_a_float_belongs():
    assert validate({"vmax": 5})[0]["vmax"] == 5.0


# ── what it refuses ───────────────────────────────────────────────────────────


def test_an_invented_field_is_dropped():
    fields, _ = validate({"vmin": 3.0, "thermal_pad": True, "price_estimate": 2.5})

    assert set(fields) == {"vmin"}


def test_a_value_with_a_unit_suffix_is_dropped_rather_than_parsed():
    """The prompt demands base SI units. A string here means it ignored that."""
    assert "vmin" not in validate({"vmin": "1.08V"})[0]


def test_a_role_outside_the_vocabulary_is_dropped():
    assert "role" not in validate({"role": "controller"})[0]


def test_a_topology_outside_the_vocabulary_is_dropped():
    assert "topology" not in validate({"topology": "magic"})[0]


def test_an_efficiency_outside_zero_to_one_is_dropped():
    assert "efficiency" not in validate({"efficiency": 92})[0]
    assert validate({"efficiency": 0.92})[0]["efficiency"] == 0.92


def test_a_boolean_is_never_a_number():
    assert "pins_required" not in validate({"pins_required": True})[0]


def test_a_stated_non_synchronous_rectifier_survives_normalisation():
    """False is data here, not an integer masquerading as a measurement."""
    fields, _ = validate({"synchronous": False})

    assert fields["synchronous"] is False


@pytest.mark.parametrize("raw", ({"synchronous": None}, {}))
def test_an_unstated_synchronous_rectifier_stays_null(raw):
    """Both '-' and an absent distributor parameter mean not stated."""
    fields, _ = validate(raw)

    assert "synchronous" not in fields


@pytest.mark.parametrize("rectifier", ("-", None))
def test_a_dash_or_absent_synchronous_rectifier_parameter_normalises_to_none(
    rectifier, monkeypatch
):
    specs = dict(SPECS)
    if rectifier is not None:
        specs["Synchronous Rectifier"] = rectifier
    candidate = replace(CANDIDATE, category="Power Management (PMIC)", specs=specs)

    async def reply(*_args):
        return {**REGULATOR_REPLY, "synchronous": None}

    monkeypatch.setattr(normalize.llm, "available", lambda: True)
    monkeypatch.setattr(normalize.llm, "complete_json", reply)
    monkeypatch.setattr(normalize.search, "enrich", _plain)

    assert asyncio.run(normalize.normalize(candidate, use_cache=False)).synchronous is None


def test_a_multi_value_topology_survives_validation():
    assert validate({"topology": "Boost、Buck"})[0]["topology"] == "boost、buck"


def test_provenance_citing_a_parameter_that_was_never_sent_is_dropped():
    """Evidence must never quote a field the distributor did not send."""
    _, provenance = validate(
        {"vmin": 1.08, "provenance": {"vmin": "Voltage - Invented"}}
    )

    assert provenance == {}


def test_provenance_for_a_field_that_was_rejected_is_dropped_too():
    _, provenance = validate(
        {"role": "controller", "provenance": {"role": "Interface"}}
    )

    assert provenance == {}


def test_a_reply_of_nothing_useful_yields_nothing():
    fields, provenance = validate({"provenance": {"vmin": "Voltage - Supply"}})

    assert fields == {} and provenance == {}


REGULATOR_REPLY = {
    "topology": "buck",
    "vout_min": 23.0,
    "vout_max": 24.0,
    "i_max": 0.03,
    "efficiency": 0.9,
    "synchronous": False,
    "role": "passive",
    "interfaces": ["i2c"],
    "vmin": 3.0,
    "vmax": 5.0,
    "temp_min": -40.0,
    "temp_max": 125.0,
}


async def _model_reply(*_args):
    return REGULATOR_REPLY


def _normalised(candidate, monkeypatch):
    monkeypatch.setattr(normalize.llm, "available", lambda: True)
    monkeypatch.setattr(normalize.llm, "complete_json", _model_reply)
    monkeypatch.setattr(normalize.search, "enrich", _plain)
    return asyncio.run(normalize.normalize(candidate, use_cache=False))


# ── fields that belong only to regulators ────────────────────────────────────


def test_a_connector_discards_modelled_regulator_fields_but_keeps_its_own_fields(monkeypatch):
    """AKZ25V15R was a screw terminal returned by a regulator query and cached as a buck."""
    candidate = replace(CANDIDATE, mpn="AKZ25V15R", category="Connectors", subcategory="Terminal")

    part = _normalised(candidate, monkeypatch)

    assert all(getattr(part, field) is None for field in (
        "topology", "vout_min", "vout_max", "i_max", "efficiency", "synchronous"
    ))
    assert part.role == "passive"
    assert part.interfaces == ("I2C",)
    assert (part.vmin, part.vmax, part.temp_min, part.temp_max) == (3.0, 5.0, -40.0, 125.0)


def test_a_pmic_candidate_keeps_modelled_regulator_fields(monkeypatch):
    candidate = replace(CANDIDATE, mpn="BUCK-1", category="Power Management (PMIC)")

    part = _normalised(candidate, monkeypatch)

    assert (part.topology, part.vout_min, part.vout_max, part.i_max, part.efficiency, part.synchronous) == (
        "buck", 23.0, 24.0, 0.03, 0.9, False
    )
    assert part.role == "passive"
    assert part.interfaces == ("I2C",)
    assert (part.vmin, part.vmax, part.temp_min, part.temp_max) == (3.0, 5.0, -40.0, 125.0)


def test_an_uncategorised_candidate_keeps_modelled_regulator_fields(monkeypatch):
    candidate = replace(CANDIDATE, mpn="UNKNOWN-1", category="", subcategory="")

    part = _normalised(candidate, monkeypatch)

    assert (part.topology, part.vout_min, part.vout_max, part.i_max, part.efficiency, part.synchronous) == (
        "buck", 23.0, 24.0, 0.03, 0.9, False
    )
    assert part.role == "passive"
    assert part.interfaces == ("I2C",)
    assert (part.vmin, part.vmax, part.temp_min, part.temp_max) == (3.0, 5.0, -40.0, 125.0)


# ── degraded mode ─────────────────────────────────────────────────────────────


def test_without_an_llm_the_payload_fields_still_arrive(monkeypatch):
    """No key is a degraded mode, not a crash — fewer answers, no wrong ones."""
    monkeypatch.setattr(normalize.llm, "available", lambda: False)
    monkeypatch.setattr(normalize.search, "enrich", _plain)

    part = asyncio.run(normalize.normalize(CANDIDATE, use_cache=False))

    assert part.mpn == "SHT40-AD1B-R2"
    assert part.stock == 20760
    assert part.package == "DFN-4-EP(1.5x1.5)"
    assert part.raw == SPECS
    assert part.vmin is None, "unparsed, so the engine reports it as unchecked"
    assert part.provenance == {}


def test_the_datasheet_falls_back_to_the_product_page(monkeypatch):
    """JLCPCB publishes no datasheet; a page a judge can open beats a null."""
    monkeypatch.setattr(normalize.llm, "available", lambda: False)
    monkeypatch.setattr(normalize.search, "enrich", _plain)

    part = asyncio.run(normalize.normalize(CANDIDATE, use_cache=False))

    assert part.datasheet == "https://jlcpcb.com/partdetail/C2909890"


def test_a_real_datasheet_is_preferred_when_one_exists(monkeypatch):
    monkeypatch.setattr(normalize.llm, "available", lambda: False)

    async def found(_mpn):
        return normalize.search.Enrichment(datasheet="https://sensirion.com/sht40.pdf")

    monkeypatch.setattr(normalize.search, "enrich", found)
    part = asyncio.run(normalize.normalize(CANDIDATE, use_cache=False))

    assert part.datasheet == "https://sensirion.com/sht40.pdf"


async def _plain(_mpn):
    return normalize.search.Enrichment()


# ── dossier facts fill only missing live fields ───────────────────────────────


def test_a_live_value_wins_over_a_dossier_value(monkeypatch):
    monkeypatch.setattr(normalize.llm, "available", lambda: False)
    monkeypatch.setattr(normalize.search, "enrich", _plain)

    async def lookup(_mpn):
        return [{"field": "package", "value": "SOT-23-5", "source": "old listing"}]

    part = asyncio.run(normalize.normalize(CANDIDATE, use_cache=False, dossier_lookup=lookup))

    assert part.package == "DFN-4-EP(1.5x1.5)"


def test_a_dossier_value_fills_a_field_absent_from_the_live_payload(monkeypatch):
    monkeypatch.setattr(normalize.llm, "available", lambda: False)
    monkeypatch.setattr(normalize.search, "enrich", _plain)

    async def lookup(_mpn):
        return [{"field": "temp_max", "value": "125.0", "source": "TI datasheet SLVS123"}]

    part = asyncio.run(normalize.normalize(CANDIDATE, use_cache=False, dossier_lookup=lookup))

    assert part.temp_max == 125.0
    assert part.provenance["temp_max"] == (
        "Continuity dossier — learned in an earlier run (TI datasheet SLVS123)"
    )
    assert part.cite("sensor", "temp_max")[0].source == part.provenance["temp_max"]


def test_an_empty_dossier_keeps_today_s_missing_field_behaviour(monkeypatch):
    monkeypatch.setattr(normalize.llm, "available", lambda: False)
    monkeypatch.setattr(normalize.search, "enrich", _plain)

    async def lookup(_mpn):
        return []

    part = asyncio.run(normalize.normalize(CANDIDATE, use_cache=False, dossier_lookup=lookup))

    assert part.temp_max is None
    assert part.provenance == {}


# ── datasheet thermal facts ───────────────────────────────────────────────────


def test_cached_datasheet_theta_ja_reaches_the_part_spec(monkeypatch):
    fact = datasheet.ThermalFact(116.3, "RθJA Junction-to-ambient 116.3", "SOIC-8")
    datasheet._save(CANDIDATE.mpn, fact)
    monkeypatch.setattr(normalize.llm, "available", lambda: False)
    monkeypatch.setattr(normalize.search, "enrich", _plain)

    part = asyncio.run(normalize.normalize(CANDIDATE, use_cache=False))

    assert part.theta_ja == 116.3
    assert part.theta_ja_source_line == "RθJA Junction-to-ambient 116.3"


def test_without_a_cached_datasheet_fact_theta_ja_stays_none(monkeypatch):
    monkeypatch.setattr(normalize.llm, "available", lambda: False)
    monkeypatch.setattr(normalize.search, "enrich", _plain)

    part = asyncio.run(normalize.normalize(CANDIDATE, use_cache=False))

    assert part.theta_ja is None
    assert part.raw == SPECS
    assert part.stock == CANDIDATE.stock


def test_a_stored_datasheet_fact_survives_a_second_normalise_without_extracting(monkeypatch):
    fact = datasheet.ThermalFact(116.3, "RθJA Junction-to-ambient 116.3", "SOIC-8")
    datasheet._save(CANDIDATE.mpn, fact)
    monkeypatch.setattr(normalize.llm, "available", lambda: False)
    monkeypatch.setattr(normalize.search, "enrich", _plain)

    async def should_not_extract(*_args, **_kwargs):
        raise AssertionError("cached thermal fact must not be extracted again")

    monkeypatch.setattr(datasheet, "theta_ja_from_text", should_not_extract)

    first = asyncio.run(normalize.normalize(CANDIDATE, use_cache=False))
    second = asyncio.run(normalize.normalize(CANDIDATE, use_cache=False))

    assert (first.theta_ja, second.theta_ja) == (116.3, 116.3)


def test_missing_package_theta_ja_starts_one_best_effort_datasheet_fetch(monkeypatch):
    candidate = replace(CANDIDATE, package="UNKNOWN-THERMAL-PACKAGE")
    fact = datasheet.ThermalFact(91.0, "RθJA Junction-to-ambient 91", candidate.package)
    urls: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def enrich(_mpn):
        return normalize.search.Enrichment(datasheet="https://example.test/regulator.pdf")

    async def fetch(url):
        urls.append(url)
        started.set()
        await release.wait()
        return b"%PDF-1.7\nfixture"

    async def extract(*_args, **kwargs):
        datasheet._save(kwargs["mpn"], fact)
        return fact

    monkeypatch.setattr(sourcing.packages, "theta_ja", lambda _package: None)
    monkeypatch.setattr(normalize.llm, "available", lambda: False)
    monkeypatch.setattr(normalize.search, "enrich", enrich)
    monkeypatch.setattr(normalize.datasheet, "fetch", fetch)
    monkeypatch.setattr(normalize.datasheet, "text_from_pdf", lambda _data: "thermal text")
    monkeypatch.setattr(normalize.datasheet, "theta_ja_from_text", extract)

    async def choose_then_complete_fetch():
        part = await choose_candidate(candidate)
        await asyncio.wait_for(started.wait(), timeout=0.1)
        release.set()
        for _ in range(5):
            await asyncio.sleep(0)
            if datasheet._load(candidate.mpn) is not None:
                break
        return part, await normalize.normalize(candidate, use_cache=False)

    first, second = asyncio.run(choose_then_complete_fetch())

    assert first.theta_ja is None
    assert second.theta_ja == 91.0
    assert urls == ["https://example.test/regulator.pdf"]


# ── live stock ───────────────────────────────────────────────────────────────


def test_live_stock_replaces_indexed_stock_and_r6_fails_on_the_live_figure(monkeypatch):
    """R6 must judge the real-time value, not the stale search-index value."""
    monkeypatch.setattr(normalize.llm, "available", lambda: False)
    monkeypatch.setattr(normalize.search, "enrich", _plain)

    async def live(_mpn):
        return 19

    monkeypatch.setattr(normalize.search, "live_stock", live)
    part = asyncio.run(normalize.normalize(CANDIDATE, use_cache=False))
    live_board = usb_board(
        regulator=parts.ap2112k(), loads={"sensor": part}, requirements=Requirements(min_stock=100)
    )
    indexed_board = usb_board(
        regulator=parts.ap2112k(),
        loads={"sensor": replace(part, stock=CANDIDATE.stock)},
        requirements=Requirements(min_stock=100),
    )

    assert part.stock == 19
    assert rules.availability(live_board)[-1].status == "fail"
    assert rules.availability(indexed_board)[-1].status == "pass"


def test_a_live_stock_figure_of_zero_is_not_treated_as_missing(monkeypatch):
    monkeypatch.setattr(normalize.llm, "available", lambda: False)
    monkeypatch.setattr(normalize.search, "enrich", _plain)

    async def live(_mpn):
        return 0

    monkeypatch.setattr(normalize.search, "live_stock", live)

    assert asyncio.run(normalize.normalize(CANDIDATE, use_cache=False)).stock == 0


def test_a_live_stock_tool_error_keeps_the_indexed_figure_and_normalises(
    monkeypatch, no_live_stock_lookup
):
    monkeypatch.setattr(normalize.llm, "available", lambda: False)
    monkeypatch.setattr(normalize.search, "enrich", _plain)

    async def call_tool(*_args):
        raise mcp.ToolError("live stock unavailable")

    monkeypatch.setattr(mcp, "call_tool", call_tool)
    monkeypatch.setattr(normalize.search, "live_stock", no_live_stock_lookup)

    part = asyncio.run(normalize.normalize(CANDIDATE, use_cache=False))

    assert (part.mpn, part.stock) == (CANDIDATE.mpn, CANDIDATE.stock)


def test_no_exact_live_stock_match_keeps_the_indexed_figure(monkeypatch, no_live_stock_lookup):
    monkeypatch.setattr(normalize.llm, "available", lambda: False)
    monkeypatch.setattr(normalize.search, "enrich", _plain)

    async def call_tool(*_args):
        return {"results": [{"model": "SHT40-AD1B", "stock": 19}]}

    monkeypatch.setattr(mcp, "call_tool", call_tool)
    monkeypatch.setattr(normalize.search, "live_stock", no_live_stock_lookup)

    assert asyncio.run(normalize.normalize(CANDIDATE, use_cache=False)).stock == CANDIDATE.stock


def test_live_lookup_starts_before_the_llm_returns(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(normalize.llm, "available", lambda: True)
    monkeypatch.setattr(normalize.search, "enrich", _plain)

    async def live(_mpn):
        events.append("live lookup started")
        await asyncio.sleep(0)
        return None

    async def reply(*_args):
        assert "live lookup started" in events
        events.append("llm returned")
        return {}

    monkeypatch.setattr(normalize.search, "live_stock", live)
    monkeypatch.setattr(normalize.llm, "complete_json", reply)
    asyncio.run(normalize.normalize(CANDIDATE, use_cache=False))

    assert events.index("live lookup started") < events.index("llm returned")


def test_live_stock_is_not_frozen_by_the_parse_cache(monkeypatch, tmp_path):
    values = iter((200, 19))
    calls: list[str] = []
    monkeypatch.setattr(normalize, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(normalize.llm, "available", lambda: True)
    monkeypatch.setattr(normalize.llm, "complete_json", _model_reply)
    monkeypatch.setattr(normalize.search, "enrich", _plain)

    async def live(mpn):
        calls.append(mpn)
        return next(values)

    monkeypatch.setattr(normalize.search, "live_stock", live)
    first = asyncio.run(normalize.normalize(CANDIDATE))
    second = asyncio.run(normalize.normalize(CANDIDATE))

    assert (first.stock, second.stock) == (200, 19)
    assert calls == [CANDIDATE.mpn, CANDIDATE.mpn]


# ── cache ─────────────────────────────────────────────────────────────────────


def test_the_cache_round_trips_and_restores_tuples(monkeypatch, tmp_path):
    """Cached by MPN for determinism — the same payload must parse the same way twice."""
    monkeypatch.setattr(normalize, "CACHE_DIR", tmp_path)
    normalize._save("SHT40-AD1B-R2", {"vmin": 1.08, "interfaces": ("I2C",)}, {"vmin": "V"})

    fields, provenance = normalize._load("SHT40-AD1B-R2")

    assert fields["interfaces"] == ("I2C",)
    assert provenance == {"vmin": "V"}


def test_a_poisoned_connector_cache_entry_is_repaired_when_it_is_read(monkeypatch, tmp_path):
    """A connector cached as a regulator must not keep declaring a rail on later runs."""
    monkeypatch.setattr(normalize, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(normalize.search, "enrich", _plain)
    connector = replace(CANDIDATE, mpn="AKZ25V15R", category="Connectors", subcategory="Terminal")
    normalize._save("AKZ25V15R", {**REGULATOR_REPLY, "interfaces": ("I2C",)}, {})

    part = asyncio.run(normalize.normalize(connector))

    assert all(getattr(part, field) is None for field in (
        "topology", "vout_min", "vout_max", "i_max", "efficiency", "synchronous"
    ))
    assert part.role == "passive"
    assert part.interfaces == ("I2C",)
    assert (part.vmin, part.vmax, part.temp_min, part.temp_max) == (3.0, 5.0, -40.0, 125.0)


def test_a_corrupt_cache_entry_is_ignored_rather_than_fatal(monkeypatch, tmp_path):
    monkeypatch.setattr(normalize, "CACHE_DIR", tmp_path)
    normalize._cache_path("X").parent.mkdir(parents=True, exist_ok=True)
    normalize._cache_path("X").write_text("{not json")

    assert normalize._load("X") is None


def test_an_mpn_with_awkward_characters_is_a_safe_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(normalize, "CACHE_DIR", tmp_path)

    assert "/" not in normalize._cache_path("TPS/62825 DMQR").name


# ── pins ──────────────────────────────────────────────────────────────────────


def test_gpio_count_ignores_power_and_unconnected_pins():
    """39 package pins is not 39 GPIO — R3 would over-budget by nearly half."""
    esp32 = ("GND", "3V3", "EN", "SENSOR_VP", "IO34", "IO35", "IO32", "NC", "IO33")

    assert normalize.gpio_count(esp32) == 4


def test_gpio_count_handles_st_style_names():
    assert normalize.gpio_count(("VBAT", "NRST", "PA0-WKUP", "PA1", "PC13")) == 3


def test_no_pinout_means_unknown_not_zero():
    """Zero GPIO would fail R3 on every board; unknown makes it report unchecked."""
    assert normalize.gpio_count(()) is None
    assert normalize.gpio_count(("GND", "VCC")) is None


# ── lifecycle ─────────────────────────────────────────────────────────────────


def test_lifecycle_is_unknown_unless_a_source_says_otherwise(monkeypatch):
    """Asserting "active" would silence R6's end-of-life warning on every part."""
    monkeypatch.setattr(normalize.llm, "available", lambda: False)

    async def nothing(_mpn):
        return normalize.search.Enrichment()

    monkeypatch.setattr(normalize.search, "enrich", nothing)
    part = asyncio.run(normalize.normalize(CANDIDATE, use_cache=False))

    assert part.lifecycle == "unknown"


def test_a_sourced_lifecycle_is_used(monkeypatch):
    monkeypatch.setattr(normalize.llm, "available", lambda: False)

    async def nrnd(_mpn):
        return normalize.search.Enrichment(datasheet=None, lifecycle="nrnd")

    monkeypatch.setattr(normalize.search, "enrich", nrnd)
    part = asyncio.run(normalize.normalize(CANDIDATE, use_cache=False))

    assert part.lifecycle == "nrnd"


def test_an_enrichment_failure_never_kills_the_part(monkeypatch):
    """A `cse_search` timeout was taking down whole runs: the UI showed ERROR after a
    successful search and parse, because a datasheet *link* lookup threw."""
    monkeypatch.setattr(normalize.llm, "available", lambda: False)

    async def exploding(_mpn):
        raise RuntimeError("cse_search unreachable after 3 attempts")

    monkeypatch.setattr(normalize.search, "enrich", exploding)
    part = asyncio.run(normalize.normalize(CANDIDATE, use_cache=False))

    assert part.mpn == "SHT40-AD1B-R2"
    assert part.datasheet == "https://jlcpcb.com/partdetail/C2909890"
    assert part.lifecycle == "unknown"


# ── output voltage: a range, not a setpoint ───────────────────────────────────
#
# TPS5430DDAR arrived from a live run typed as `vout=32.04`. It is an *adjustable*
# buck (1.221 V–32 V), so 32.04 is the top of its adjustment range and not an output
# at all. The reviewer then wrote a fluent rationale about "the required output is
# 32.04 V" on a board whose highest rail is 5 V.


def test_an_adjustable_regulator_keeps_both_ends_of_its_range():
    fields, _ = validate({"vout_min": 1.221, "vout_max": 32.0})

    assert fields["vout_min"] == 1.221
    assert fields["vout_max"] == 32.0


def test_an_adjustable_regulator_has_no_single_output_voltage():
    """`vout` is what the part *produces*. An adjustable one produces nothing until set."""
    part = normalize.PartSpec(
        mpn="TPS5430DDAR", manufacturer="TI", description="buck", category="DC-DC",
        vout_min=1.221, vout_max=32.0,
    )

    assert part.vout is None


def test_a_fixed_regulator_reports_its_output():
    part = normalize.PartSpec(
        mpn="AMS1117-3.3", manufacturer="AMS", description="ldo", category="LDO",
        vout_min=3.3, vout_max=3.3,
    )

    assert part.vout == 3.3


def test_a_single_stated_output_fixes_both_ends():
    """A part quoting one output figure is fixed at it, not adjustable up to it."""
    fields, _ = validate({"vout": 3.3})

    assert fields["vout_min"] == 3.3
    assert fields["vout_max"] == 3.3


def test_an_output_range_the_wrong_way_round_is_dropped():
    fields, _ = validate({"vout_min": 32.0, "vout_max": 1.221})

    assert "vout_min" not in fields and "vout_max" not in fields


# ── "Output Type" decides what one output figure means ────────────────────────
#
# TPS61040DBVR is an adjustable boost, 1.8–28 V. JLCPCB states "Output Voltage: 28V"
# with "Output Type: Adjustable", and collapsing that to a fixed 28 V made `produces(3.3)`
# return False — rejecting the exact part a boost repair needs.

ADJUSTABLE = {"Output Voltage": "28V", "Output Type": "Adjustable"}
FIXED = {"Output Voltage": "3.3V", "Output Type": "Fixed"}


def test_an_adjustable_part_reads_one_figure_as_a_maximum():
    fields, _ = normalize.validate({"vout_min": 28.0, "vout_max": 28.0}, ADJUSTABLE)

    assert fields["vout_max"] == 28.0
    assert fields.get("vout_min") is None, "an adjustable part's minimum is not its maximum"


def test_a_fixed_part_reads_one_figure_as_a_setpoint():
    fields, _ = normalize.validate({"vout_min": 3.3, "vout_max": 3.3}, FIXED)

    assert fields["vout_min"] == fields["vout_max"] == 3.3


def test_an_adjustable_part_stating_a_real_range_keeps_both_ends():
    fields, _ = normalize.validate({"vout_min": 1.8, "vout_max": 28.0}, ADJUSTABLE)

    assert fields["vout_min"] == 1.8 and fields["vout_max"] == 28.0


def test_produces_is_unchecked_when_only_the_maximum_is_known():
    part = normalize.PartSpec(
        mpn="TPS61040DBVR", manufacturer="TI", description="boost", category="DC-DC",
        vout_max=28.0,
    )

    assert part.produces(3.3) is None, "one-sided: not provable either way"


def test_produces_still_refuses_a_voltage_above_a_known_maximum():
    part = normalize.PartSpec(
        mpn="TPS61040DBVR", manufacturer="TI", description="boost", category="DC-DC",
        vout_max=28.0,
    )

    assert part.produces(30.0) is False
