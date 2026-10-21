"""Search layer. Live calls are skipped unless CONTINUITY_LIVE=1 is set."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from continuity.parts import fixtures, mcp, search
from continuity.graph.sourcing import find as source_find

live = pytest.mark.skipif(
    os.environ.get("CONTINUITY_LIVE") != "1", reason="set CONTINUITY_LIVE=1 for network tests"
)


def run(coro):
    return asyncio.run(coro)


# ── pure ──────────────────────────────────────────────────────────────────────


def test_a_numeric_spec_filter_is_sent_as_a_string():
    """The server rejects a bare number, and units change the meaning of the query."""
    assert search.SpecFilter("Output Current", ">=", 1).as_dict() == {
        "name": "Output Current",
        "op": ">=",
        "value": "1",
    }


def test_fixture_keys_ignore_argument_order():
    a = fixtures.key_for("jlc_search", {"query": "ldo", "limit": 3})
    b = fixtures.key_for("jlc_search", {"limit": 3, "query": "ldo"})

    assert a == b


def test_replay_without_a_recording_is_an_error_not_a_live_call(monkeypatch, tmp_path):
    """A fixture run that silently reaches the network looks offline until it isn't."""
    monkeypatch.setattr(fixtures, "FIXTURE_DIR", tmp_path)

    with pytest.raises(fixtures.MissingFixture):
        fixtures.require("jlc_search", {"query": "nothing recorded"})


def test_a_recording_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(fixtures, "FIXTURE_DIR", tmp_path)
    fixtures.save("jlc_search", {"query": "ldo"}, {"results": [{"model": "AMS1117-3.3"}]})

    assert fixtures.require("jlc_search", {"query": "ldo"})["results"][0]["model"] == "AMS1117-3.3"


def test_a_candidate_links_to_a_page_a_person_can_open():
    candidate = search._candidate({"lcsc": "C6186", "model": "AMS1117-3.3"})

    assert candidate.product_url == "https://jlcpcb.com/partdetail/C6186"


def test_rows_without_a_part_number_are_dropped():
    assert search._candidate({"lcsc": "C1", "model": "X"}).mpn == "X"


# ── live stock ───────────────────────────────────────────────────────────────


def test_live_stock_uses_the_exact_model_match_and_preserves_zero(monkeypatch):
    called = []

    async def call_tool(tool, arguments):
        called.append((tool, arguments))
        return {
            "results": [
                {"model": "MT3608L", "stock": 100},
                {"model": "mt3608", "stock": 0},
                {"model": "MT3608B", "stock": 200},
            ]
        }

    monkeypatch.setattr(mcp, "call_tool", call_tool)

    assert run(search.live_stock("MT3608")) == 0
    assert called == [("jlc_stock_check", {"query": "MT3608", "limit": 5})]


def test_live_stock_returns_none_on_a_tool_error(monkeypatch):
    async def call_tool(*_args):
        raise mcp.ToolError("unavailable")

    monkeypatch.setattr(mcp, "call_tool", call_tool)

    assert run(search.live_stock("SHT40-AD1B-R2")) is None


def test_live_stock_returns_none_when_no_result_matches_the_exact_mpn(monkeypatch):
    async def call_tool(*_args):
        return {"results": [{"model": "MT3608L", "stock": 100}]}

    monkeypatch.setattr(mcp, "call_tool", call_tool)

    assert run(search.live_stock("MT3608")) is None


# ── Mouser lifecycle enrichment ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("stated", "expected"),
    [
        ("New Product", "active"),
        ("New at Mouser", "active"),
        ("  new PRODUCT ", "active"),
        ("Not Recommended for New Designs", "nrnd"),
        ("End of Life", "obsolete"),
    ],
)
def test_mouser_lifecycle_values_are_normalized(monkeypatch, stated, expected):
    async def call_tool(tool, arguments):
        assert (tool, arguments) == ("mouser_get_part", {"part_number": "PART-1"})
        return {
            "results": [
                {
                    "mfr_part_number": "PART-1",
                    "datasheet_url": "https://example.test/PART-1.pdf",
                    "lifecycle": stated,
                }
            ]
        }

    monkeypatch.setattr(mcp, "call_tool", call_tool)

    assert run(search.enrich("PART-1")).lifecycle == expected


def test_an_unrecognized_mouser_lifecycle_remains_unknown(monkeypatch):
    from continuity.parts.normalize import _from_payload

    async def call_tool(tool, arguments):
        assert (tool, arguments) == ("mouser_get_part", {"part_number": "PART-1"})
        return {
            "results": [
                {
                    "mfr_part_number": "PART-1",
                    "datasheet_url": "https://example.test/PART-1.pdf",
                    "lifecycle": "Still Listed",
                }
            ]
        }

    monkeypatch.setattr(mcp, "call_tool", call_tool)

    enrichment = run(search.enrich("PART-1"))
    candidate = search._candidate({"lcsc": "C1", "model": "PART-1"})

    assert enrichment.lifecycle is None
    assert _from_payload(candidate, enrichment.datasheet, enrichment.lifecycle, stock=None)["lifecycle"] == "unknown"


# ── datasheet citations ───────────────────────────────────────────────────────


def test_cse_search_rejects_a_google_search_and_uses_the_product_page(monkeypatch):
    from continuity.parts.normalize import _from_payload

    async def call_tool(tool, arguments):
        assert (tool, arguments) == ("cse_search", {"query": "TPS54331"})
        return {"results": [{"datasheet_url": "https://www.google.com/search?q=TPS54331"}]}

    monkeypatch.setattr(mcp, "call_tool", call_tool)

    datasheet = run(search.datasheet_for("TPS54331"))
    candidate = search._candidate({"lcsc": "C1", "model": "TPS54331"})

    assert datasheet is None
    assert _from_payload(candidate, datasheet, lifecycle=None, stock=None)["datasheet"] == candidate.product_url


def test_cse_search_rejects_a_url_without_a_host(monkeypatch):
    async def call_tool(tool, arguments):
        assert (tool, arguments) == ("cse_search", {"query": "PART-1"})
        return {"results": [{"datasheet_url": "https:///PART-1.pdf"}]}

    monkeypatch.setattr(mcp, "call_tool", call_tool)

    assert run(search.datasheet_for("PART-1")) is None


def test_cse_search_rejects_a_malformed_url(monkeypatch):
    async def call_tool(tool, arguments):
        assert (tool, arguments) == ("cse_search", {"query": "PART-1"})
        return {"results": [{"datasheet_url": "https://[not-a-host"}]}

    monkeypatch.setattr(mcp, "call_tool", call_tool)

    assert run(search.datasheet_for("PART-1")) is None


def test_mouser_search_url_is_rejected_before_the_cse_fallback(monkeypatch):
    calls = []

    async def call_tool(tool, arguments):
        calls.append((tool, arguments))
        if tool == "mouser_get_part":
            return {
                "results": [
                    {
                        "mfr_part_number": "PART-1",
                        "datasheet_url": "https://www.bing.com/search?query=PART-1",
                        "lifecycle": "New Product",
                    }
                ]
            }
        assert (tool, arguments) == ("cse_search", {"query": "PART-1"})
        return {"results": []}

    monkeypatch.setattr(mcp, "call_tool", call_tool)

    enrichment = run(search.enrich("PART-1"))

    assert enrichment == search.Enrichment(datasheet=None, lifecycle="active")
    assert calls == [
        ("mouser_get_part", {"part_number": "PART-1"}),
        ("cse_search", {"query": "PART-1"}),
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.ti.com/lit/ds/symlink/tps54331.pdf",
        "https://pdf1.alldatasheet.com/datasheet-pdf/view/123/PART.html",
        "https://vendor.example.com/google/part.pdf",
    ],
)
def test_cse_search_keeps_openable_document_urls(monkeypatch, url):
    async def call_tool(tool, arguments):
        assert (tool, arguments) == ("cse_search", {"query": "PART-1"})
        return {"results": [{"datasheet_url": url}]}

    monkeypatch.setattr(mcp, "call_tool", call_tool)

    assert run(search.datasheet_for("PART-1")) == url


def test_enrichment_stays_usable_when_every_datasheet_url_is_rejected(monkeypatch):
    async def call_tool(tool, arguments):
        if tool == "mouser_get_part":
            return {
                "results": [
                    {
                        "mfr_part_number": "PART-1",
                        "datasheet_url": "https:///PART-1.pdf",
                    }
                ]
            }
        assert (tool, arguments) == ("cse_search", {"query": "PART-1"})
        return {
            "results": [
                {"datasheet_url": "ftp://vendor.example.com/PART-1.pdf"},
                {"datasheet_url": "https://duckduckgo.com/?q=PART-1"},
            ]
        }

    monkeypatch.setattr(mcp, "call_tool", call_tool)

    assert run(search.enrich("PART-1")) == search.Enrichment()


# ── live ──────────────────────────────────────────────────────────────────────


@live
def test_search_returns_real_parts_with_specs():
    rows = run(search.search("3.3V LDO regulator", package="SOT-223", limit=3))

    assert rows
    assert all(row.mpn and row.stock >= 0 for row in rows)
    assert any(row.specs for row in rows), "specs are what the normaliser consumes"


@live
def test_a_parametric_filter_narrows_the_query():
    rows = run(
        search.search(
            "buck converter", spec_filters=[search.SpecFilter("Output Current", ">=", "1A")], limit=3
        )
    )

    assert rows


@live
def test_a_descriptive_query_still_finds_something():
    """The server's smart parser pins a subcategory and matches the rest inside it, so
    extra words can cost a match. A planner writes English; it must not get zero."""
    for query in (
        "small OLED display module I2C",
        "temperature and humidity sensor over I2C",
        "1A buck converter 3.3V output",
    ):
        assert run(search.search(query, limit=2)), query


def test_fallback_queries_prefer_the_servers_own_parse():
    payload = {"parsed": {"detected": {"component_type": "bluetooth module"}}}

    attempts = search._fallback_queries("wifi bluetooth module thing", payload)

    assert attempts[0] == "bluetooth module"
    assert "wifi bluetooth" in attempts, "then progressively shorter prefixes"


def test_fallback_never_repeats_the_query_that_already_failed():
    assert search._fallback_queries("ESP32", None) == []


@live
def test_datasheets_come_from_the_ecad_index_not_jlcpcb():
    """JLCPCB publishes no datasheet link; evidence needs somewhere to point."""
    assert (run(search.datasheet_for("AMS1117-3.3")) or "").startswith("http")


# ── a topology change must filter, not phrase ─────────────────────────────────
#
# `change_topology: boost` used to build the query "3.3V boost converter". Live, that
# returns XL1509-5.0E1, TPS5430DDAR, XL1509-ADJE1 — all bucks. JLCPCB's text search
# ignores the word, so the repair got the same buck back, failed identically, and burned
# every repair attempt until the fence stopped it. JLCPCB does publish `Topology` as a
# filterable parameter; filtering on it returns real boost parts.


def test_a_topology_change_becomes_a_spec_filter():
    from continuity.graph import sourcing

    query, filters, _ = sourcing._push_down("3.3v regulator", {"topology": "boost"})

    assert any(f.name == "Topology" and f.value == "Boost" for f in filters)


def test_a_topology_change_does_not_narrow_the_query_text():
    """Extra words reduce results — the planner prompt says so and this ignored it."""
    from continuity.graph import sourcing

    query, _, _ = sourcing._push_down("3.3v regulator", {"topology": "boost", "vout": 3.3})

    assert "boost" not in query.lower(), "topology belongs in the filter, not the text"
    assert "3.3v" not in query.lower(), "a voltage in the query text filters nothing"


def test_a_linear_constraint_has_no_topology_filter():
    """LDOs are a different JLCPCB category, not a Topology value."""
    from continuity.graph import sourcing

    query, filters, _ = sourcing._push_down("regulator", {"topology": "ldo"})

    assert not any(f.name == "Topology" for f in filters)
    assert "ldo" in query.lower()


def test_an_output_current_constraint_still_filters():
    from continuity.graph import sourcing

    _, filters, _ = sourcing._push_down("regulator", {"topology": "boost", "i_out_min": 0.6})

    assert any(f.name == "Output Current" for f in filters)


# ── reading a range out of the payload, without a model ───────────────────────
#
# JLCPCB stores a whole range in one string: "Voltage - Supply": "4.5V~40V". A spec
# filter on that matches the range MINIMUM, so "accepts at least 48 V" is not
# expressible — `Voltage - Supply >= 48V` returns 0 hits, measured live. The payload
# carries the ceiling; it just needs splitting.
#
# These values pick which candidates to LOOK at. They never become verdicts — the engine
# still checks the normalised PartSpec, which carries provenance.


def test_a_range_gives_both_ends():
    from continuity.parts import payload

    assert payload.volt_range("4.5V~40V") == (4.5, 40.0)


def test_spaces_and_decimals_survive():
    from continuity.parts import payload

    assert payload.volt_range("2.5V ~ 6.0V") == (2.5, 6.0)


def test_a_single_figure_is_a_ceiling():
    """Same reading the normaliser is told to use: one figure on a supply is a maximum."""
    from continuity.parts import payload

    assert payload.volt_range("15V") == (None, 15.0)


def test_millivolts_are_scaled():
    from continuity.parts import payload

    assert payload.volt_range("800mV~3.6V") == (0.8, 3.6)


def test_unparseable_text_is_unknown_not_zero():
    from continuity.parts import payload

    assert payload.volt_range("see datasheet") == (None, None)
    assert payload.volt_range(None) == (None, None)


def test_a_part_that_provably_cannot_take_the_input_is_dropped():
    from continuity.parts import payload

    assert payload.accepts_input({"Voltage - Supply": "4.5V~40V"}, 48.0) is False
    assert payload.accepts_input({"Voltage - Supply": "4.5V~60V"}, 48.0) is True


def test_a_part_that_states_nothing_is_kept():
    """Unknown is not a reason to discard — the engine will report it as unchecked."""
    from continuity.parts import payload

    assert payload.accepts_input({}, 48.0) is None


# ── reading an operating-temperature ceiling out of the payload ──────────────


def test_an_operating_temperature_range_reaches_its_hot_end():
    from continuity.parts import payload

    specs = {"Operating Temperature": "-40℃~+85℃"}

    assert payload.rated_to(specs, 85) is True
    assert payload.rated_to(specs, 86) is False


def test_an_operating_temperature_qualifier_does_not_break_its_range():
    from continuity.parts import payload

    assert payload.rated_to({"Operating Temperature": "-40℃~+150℃@(TJ)"}, 150) is True


def test_an_unstated_temperature_is_kept_not_discarded():
    from continuity.graph import sourcing
    from continuity.parts import payload

    assert payload.rated_to({}, 85) is None
    kept = sourcing.viable([_candidate("MYSTERY")], {"rated_to": 85})

    assert [candidate.mpn for candidate in kept] == ["MYSTERY"]


# ── local filtering, where the server's filters cannot reach ──────────────────


def _candidate(mpn: str, **specs):
    from continuity.parts.search import Candidate

    return Candidate(
        lcsc="C1", mpn=mpn, manufacturer="x", description="buck", package="SOT-23",
        category="DC-DC", subcategory="DC-DC Converters", stock=100, unit_price=1.0,
        library_type="basic", specs=specs,
    )


def test_a_part_that_cannot_take_the_input_is_filtered_out():
    from continuity.graph import sourcing

    kept = sourcing.viable(
        [
            _candidate("XL1509-5.0E1", **{"Voltage - Supply": "4.5V~40V"}),
            _candidate("LM5164DDAR", **{"Voltage - Supply": "6V~100V"}),
        ],
        {"vin_min": 48.0},
    )

    assert [c.mpn for c in kept] == ["LM5164DDAR"]


def test_an_unstated_rating_is_kept_not_discarded():
    from continuity.graph import sourcing

    kept = sourcing.viable([_candidate("MYSTERY")], {"vin_min": 48.0})

    assert len(kept) == 1, "unknown must not be read as no"


def test_a_decidable_candidate_outranks_an_undecidable_one():
    """Three valid boosts came back and the engine took the first, whose payload states
    no output minimum — leaving the board's central claim unchecked."""
    from continuity.graph import sourcing

    ranked = sourcing.viable(
        [
            _candidate("TPS61040DBVR", **{"Output Voltage": "28V", "Output Type": "Adjustable"}),
            _candidate("MT3608", **{"Output Voltage": "0.6V~28V", "Output Type": "Adjustable"}),
        ],
        {"topology": "boost"},
    )

    assert ranked[0].mpn == "MT3608", "prefer the one the engine can actually check"


def test_ranking_never_drops_anything():
    from continuity.graph import sourcing

    given = [_candidate("A"), _candidate("B", **{"Output Voltage": "3.3V"})]

    assert len(sourcing.viable(given, {})) == 2


def test_a_locally_filtered_constraint_searches_deeper():
    """Filtering six results that are all wrong leaves zero.

    Measured on the 48 V board: at limit 6 every buck returned is a 40 V part, so
    `vin_min` keeps nothing. At limit 40 there are six provably capable ones. The pool
    has to be deep enough to filter *from*, and depth is a parameter on the same single
    request rather than a second round trip.
    """
    from continuity.graph import sourcing

    assert sourcing.pool_size({"topology": "buck"}) == sourcing.CANDIDATES_PER_SLOT
    assert sourcing.pool_size({"topology": "buck", "vin_min": 48.0}) == sourcing.DEEP_POOL


def test_a_temperature_constraint_filters_a_deep_pool_without_a_server_filter(monkeypatch):
    from continuity.graph import sourcing

    calls = []

    async def fake_search(query, **kwargs):
        calls.append((query, kwargs))
        return [
            _candidate("COMMERCIAL", **{"Operating Temperature": "-40℃~+70℃"}),
            _candidate("INDUSTRIAL", **{"Operating Temperature": "-55℃~+125℃"}),
            _candidate("UNKNOWN"),
        ]

    monkeypatch.setattr(sourcing, "search", fake_search)

    found = run(source_find("wifi module", constraint={"rated_to": 85}))

    assert [candidate.mpn for candidate in found] == ["INDUSTRIAL", "UNKNOWN"]
    assert calls[0][1]["limit"] == sourcing.DEEP_POOL
    assert not any(
        spec_filter.name == "rated_to" for spec_filter in calls[0][1]["spec_filters"] or []
    )


def test_an_impossible_temperature_constraint_returns_no_wrong_part(monkeypatch):
    from continuity.graph import sourcing

    async def fake_search(query, **kwargs):
        return [_candidate("COMMERCIAL", **{"Operating Temperature": "-40℃~+70℃"})]

    monkeypatch.setattr(sourcing, "search", fake_search)

    assert run(source_find("wifi module", constraint={"rated_to": 85})) == []


def test_the_shortlist_stays_short():
    from continuity.graph import sourcing

    many = [_candidate(f"P{i}", **{"Voltage - Supply": "5V~80V"}) for i in range(40)]
    kept = sourcing.viable(many, {"vin_min": 48.0})[: sourcing.CANDIDATES_PER_SLOT]

    assert len(kept) == sourcing.CANDIDATES_PER_SLOT


# ── a repair adds to what a slot is, it does not replace it ───────────────────


def test_a_repair_constraint_layers_over_the_slots_own():
    """48 V board, 9 Aug. The reviewer chose `swap` with `vin_min: 48` and no topology —
    correctly, since it was not changing the kind of part. That dropped the slot's own
    `topology: buck`, so the re-search ran as raw text, returned a screw terminal, and
    the run escalated with "nothing matches the revised buck regulator constraint".
    """
    from continuity.graph import sourcing

    merged = sourcing.merge_constraints({"topology": "buck"}, {"vin_min": 48.0})

    assert merged == {"topology": "buck", "vin_min": 48.0}


def test_a_repair_may_still_override_the_slots_topology():
    """`change_topology: boost` on a slot planned as a buck must win."""
    from continuity.graph import sourcing

    merged = sourcing.merge_constraints({"topology": "buck"}, {"topology": "boost"})

    assert merged["topology"] == "boost"


def test_merging_tolerates_either_side_being_absent():
    from continuity.graph import sourcing

    assert sourcing.merge_constraints(None, {"vin_min": 48.0}) == {"vin_min": 48.0}
    assert sourcing.merge_constraints({"topology": "buck"}, None) == {"topology": "buck"}
    assert sourcing.merge_constraints(None, None) == {}


# ── the output side of the same check ─────────────────────────────────────────


def test_a_fixed_part_that_cannot_reach_the_rail_is_dropped():
    from continuity.parts import payload

    fixed_5v = {"Output Voltage": "5V", "Output Type": "Fixed"}

    assert payload.can_output(fixed_5v, 3.3) is False
    assert payload.can_output(fixed_5v, 5.0) is True


def test_an_adjustable_range_covering_the_rail_is_kept():
    from continuity.parts import payload

    adjustable = {"Output Voltage": "1.23V~37V", "Output Type": "Adjustable"}

    assert payload.can_output(adjustable, 3.3) is True


def test_an_adjustable_ceiling_alone_is_undecidable():
    """"28V" on an adjustable part is a ceiling, not a setpoint — its floor is unstated."""
    from continuity.parts import payload

    assert payload.can_output({"Output Voltage": "28V", "Output Type": "Adjustable"}, 3.3) is None


def test_a_vout_constraint_filters_the_shortlist():
    from continuity.graph import sourcing

    kept = sourcing.viable(
        [
            _candidate("XL1509-5.0E1", **{"Output Voltage": "5V", "Output Type": "Fixed"}),
            _candidate("XL1509-ADJE1", **{"Output Voltage": "1.25V~40V", "Output Type": "Adjustable"}),
        ],
        {"vout": 3.3},
    )

    assert [c.mpn for c in kept] == ["XL1509-ADJE1"], "a fixed 5 V part cannot make 3.3 V"


# ── a slot says what kind of part it is ───────────────────────────────────────
#
# Measured live, 10 Aug. `'environmental sensor'` returns nothing at all, so `search`
# falls back to its own first word and `'environmental'` matches RoHS marketing copy:
#
#     Circuit Protection / Fuseholders            "5x20 Environmentally Friendly Fuse Clip"
#     Connectors / Female Headers                 "2.54-1*8P Female Environmentally Friendly"
#     Hardware Fasteners / Metal Products         "Complies with RoHS and REACH environmental"
#
# Every row already carries the distributor's own category. Nothing had to be fetched to
# know these were not sensors — the slot simply had no way to say so.


def _typed(mpn: str, category: str, subcategory: str = "", **specs):
    from continuity.parts.search import Candidate

    return Candidate(
        lcsc="C1", mpn=mpn, manufacturer="x", description=mpn, package="SMD",
        category=category, subcategory=subcategory, stock=100, unit_price=1.0,
        library_type="basic", specs=specs,
    )


def test_a_sensor_slot_rejects_the_fuse_clips_it_used_to_accept():
    from continuity.graph import sourcing

    kept = sourcing.viable(
        [
            _typed("BLX-A", "Circuit Protection", "Fuseholders"),
            _typed("F0801", "Connectors", "Female Headers"),
            _typed("SHT30-DIS", "Sensors", "Temperature and Humidity Sensor"),
        ],
        {"category": "sensor"},
    )

    assert [c.mpn for c in kept] == ["SHT30-DIS"]


def test_a_sensor_slot_accepts_a_current_sensor_from_a_sibling_category():
    """JLCPCB files current sensors under `Magnetic Sensors`, not `Sensors`. One name in
    our vocabulary maps to every distributor category that satisfies it."""
    from continuity.graph import sourcing

    kept = sourcing.viable([_typed("INA181", "Magnetic Sensors", "Current Sensors")], {"category": "sensor"})

    assert len(kept) == 1


def test_a_row_that_states_no_category_is_kept():
    """Unknown is not a violation. Same direction as every other local filter: turning
    "cannot tell" into "no" is the one way this system may not guess."""
    from continuity.graph import sourcing

    assert len(sourcing.viable([_typed("MYSTERY", "")], {"category": "sensor"})) == 1


def test_a_category_outside_the_vocabulary_filters_nothing():
    from continuity.graph import sourcing

    given = [_typed("BLX-A", "Circuit Protection"), _typed("SHT30", "Sensors")]

    assert len(sourcing.viable(given, {"category": "vibes"})) == 2


def test_a_category_constraint_searches_deeper():
    """Filtering six results that are all wrong leaves zero — the `vin_min` lesson. The
    'environmental' page was six rows and none of them was a sensor."""
    from continuity.graph import sourcing

    assert sourcing.pool_size({"category": "sensor"}) == sourcing.DEEP_POOL


def test_a_category_is_never_pushed_into_the_subcategory_filter():
    """`subcategory_name` matches *subcategories*, fuzzily. Probed live: passing the
    category "Sensors" returns four VOC sensors — it silently resolved to the nearest
    subcategory name. Our vocabulary is coarse on purpose, so it stays a local filter."""
    from continuity.graph import sourcing

    query, filters, package = sourcing._push_down("environmental sensor", {"category": "sensor"})

    assert query == "environmental sensor"
    assert filters == [] and package is None


def test_every_category_name_maps_to_at_least_one_distributor_category():
    from continuity.parts import categories

    assert categories.CATEGORIES
    assert all(spec.accepts for spec in categories.CATEGORIES.values())


@live
def test_every_distributor_category_we_map_actually_exists():
    """An unrecognised spec-filter name is silently ignored by this server; a category
    name we invented would be a filter that quietly rejects everything. Check the
    vocabulary against the server's own list rather than against a memory of it."""
    from continuity.parts import categories, mcp

    payload = run(mcp.call_tool("jlc_search_help", {}))
    published = {row["name"] for row in payload["categories"]}

    mapped = {name for spec in categories.CATEGORIES.values() for name in spec.accepts}
    assert mapped <= published, f"not published by JLCPCB: {sorted(mapped - published)}"


def test_an_empty_result_names_the_category_that_was_in_force():
    """Two causes, two different fixes. A query that finds nothing is the planner's
    wording; a query that finds only the wrong kind of part is the planner's category.
    "No part found" distinguishes neither."""
    from continuity.engine.models import Slot
    from continuity.graph.nodes import _within

    slots = {
        "s": Slot(id="s", label="Sensor", tier="peripherals", constraint={"category": "sensor"}),
        "u": Slot(id="u", label="Thing", tier="peripherals"),
    }

    assert _within("s", slots) == " among sensor parts"
    assert _within("u", slots) == "", "an unconstrained slot reads exactly as before"


# ── rescue a thin shortlist with one verified subcategory search ──────────────


def _real_sourcing():
    """The default suite replaces `find`; these unit tests exercise it directly."""
    from importlib import reload
    from continuity.graph import sourcing

    return reload(sourcing)


def test_a_empty_category_shortlist_gets_one_subcategory_rescue(monkeypatch):
    """The broad query can return only wrong categories; the one rescue stays local."""
    sourcing = _real_sourcing()

    calls = []

    async def fake_search(query, **kwargs):
        calls.append((query, kwargs))
        if query:
            return [_typed("FUSE", "Circuit Protection", "Fuseholders")]
        return [
            _typed("FUSE-AGAIN", "Circuit Protection", "Fuseholders"),
            _typed("SHT31", "Sensors", "Temperature and Humidity Sensor"),
        ]

    monkeypatch.setattr(sourcing, "search", fake_search)

    found = run(sourcing.find("environmental sensor", constraint={"category": "sensor"}))

    assert [candidate.mpn for candidate in found] == ["SHT31"]
    assert len(calls) == 2
    assert calls[1][0] == ""
    assert calls[1][1]["subcategory_name"] == "Temperature and Humidity Sensor"


def test_a_rescue_appends_after_the_original_candidate_and_deduplicates(monkeypatch):
    sourcing = _real_sourcing()

    calls = []
    original = _typed("SHT30", "Sensors", "Temperature and Humidity Sensor")

    async def fake_search(query, **kwargs):
        calls.append((query, kwargs))
        return [original] if query else [original, _typed("SHT31", "Sensors")]

    monkeypatch.setattr(sourcing, "search", fake_search)

    found = run(sourcing.find("environmental sensor", constraint={"category": "sensor"}))

    assert [candidate.mpn for candidate in found] == ["SHT30", "SHT31"]
    assert len(calls) == 2


def test_a_healthy_shortlist_does_not_trigger_a_rescue_search(monkeypatch):
    sourcing = _real_sourcing()

    calls = []

    async def fake_search(query, **kwargs):
        calls.append((query, kwargs))
        return [_typed(f"SHT{i}", "Sensors") for i in range(3)]

    monkeypatch.setattr(sourcing, "search", fake_search)

    found = run(sourcing.find("environmental sensor", constraint={"category": "sensor"}))

    assert len(found) == sourcing.MIN_CANDIDATES
    assert len(calls) == 1


@pytest.mark.parametrize(
    "constraint",
    [
        {},
        {"category": "amplifier"},
        {"category": "sensor", "mpn": "SHT30"},
    ],
)
def test_constraints_that_cannot_be_rescued_do_not_search_again(monkeypatch, constraint):
    sourcing = _real_sourcing()

    calls = []

    async def fake_search(query, **kwargs):
        calls.append((query, kwargs))
        return []

    monkeypatch.setattr(sourcing, "search", fake_search)

    assert run(sourcing.find("environmental sensor", constraint=constraint)) == []
    assert len(calls) == 1


def test_rescue_subcategory_prefers_word_overlap_then_list_order():
    """List order on a tie *or* on no overlap at all, and that is deliberate.

    Requiring a non-zero overlap was tried on 12 Aug and reverted the same hour: the
    rescue's motivating case, `"environmental sensor"`, shares no word with any sensor
    subcategory, so the stricter rule switched the feature off for exactly the query it
    was built for.
    """
    sourcing = _real_sourcing()

    assert sourcing._rescue_subcategory("temperature humidity sensor", {"category": "sensor"}) == (
        "Temperature and Humidity Sensor"
    )
    assert sourcing._rescue_subcategory("capacitive moisture sensor", {"category": "sensor"}) == (
        "Temperature and Humidity Sensor"
    )


def test_search_trace_names_the_query_rescue_and_thin_shortlist():
    from continuity.graph.nodes import _rescue_search_message, _thin_shortlist_message

    assert "environmental sensor" in _rescue_search_message(
        "environmental sensor", "Temperature and Humidity Sensor"
    )
    assert "Temperature and Humidity Sensor" in _rescue_search_message(
        "environmental sensor", "Temperature and Humidity Sensor"
    )
    assert "no replacements" in _thin_shortlist_message(1)


def test_rescue_narration_arrives_before_the_second_distributor_search(monkeypatch):
    sourcing = _real_sourcing()
    calls: list[str] = []
    narrated: list[str] = []

    async def search(query, **kwargs):
        calls.append(query)
        if len(calls) == 2:
            assert narrated == ["Temperature and Humidity Sensor"]
        return []

    monkeypatch.setattr(sourcing, "search", search)

    with sourcing.narrate_rescue(narrated.append):
        run(sourcing.find("environmental sensor", constraint={"category": "sensor"}))

    assert calls == ["environmental sensor", ""]


def test_repair_search_trace_names_its_applied_constraint():
    from continuity.graph.nodes import _repair_search_message

    assert _repair_search_message("buck regulator", {"topology": "boost"}) == (
        "Re-searching with topology=boost."
    )


def test_a_topology_is_not_pushed_for_a_slot_that_is_not_a_regulator():
    """Pushing a topology *rewrites the query text*, so it may only be done for a slot
    that is actually a regulator.

    The planner stamps a topology on whatever sources a rail — the prompt asks it to
    compare rail voltages — so a PoE powered-device controller feeding a 5 V rail from
    48 V was labelled `buck`. `_push_down` then replaced "PoE controller" with "dc-dc
    converter", and the board got an 80 V buck in its PoE slot with every electrical check
    passing. Measured 13 Aug: the same slot searched on its own text returns six genuine
    PoE controllers.
    """
    from continuity.graph import sourcing

    constraint = {"category": "poe", "topology": "buck", "vin_min": 48.0}
    refined, filters, _package = sourcing._push_down("PoE controller", constraint)

    assert refined == "PoE controller", "the slot's own words survive"
    assert [f.name for f in filters] == [], "and no Topology filter narrows it to converters"


def test_a_topology_is_still_pushed_for_a_regulator():
    """The measured behaviour this guard must not disturb: a text search does not weight
    "boost", so the filter is the only thing that finds one."""
    from continuity.graph import sourcing

    refined, filters, _package = sourcing._push_down(
        "3.3V LDO regulator", {"category": "regulator", "topology": "boost"}
    )

    assert refined == "dc-dc converter"
    assert [(f.name, f.value) for f in filters] == [("Topology", "Boost")]


def test_a_topology_on_an_uncategorised_slot_is_unchanged():
    """Repairs on slots with no category keep exactly the previous behaviour."""
    from continuity.graph import sourcing

    refined, filters, _package = sourcing._push_down("regulator", {"topology": "buck"})

    assert refined == "dc-dc converter"
    assert [f.name for f in filters] == ["Topology"]


# ── the cold end of the operating range ───────────────────────────────────────


def test_rated_from_reads_the_minimum_of_a_published_range():
    from continuity.parts import payload

    industrial = {"Operating Temperature": "-40℃~+85℃"}
    commercial = {"Operating Temperature": "-25~85℃"}

    assert payload.rated_from(industrial, -40) is True
    assert payload.rated_from(commercial, -40) is False
    assert payload.rated_from(commercial, -25) is True


def test_rated_from_keeps_a_candidate_whose_payload_does_not_say():
    """Unknown must not become a rejection — the engine reports it unchecked instead."""
    from continuity.parts import payload

    assert payload.rated_from({}, -40) is None
    assert payload.rated_from({"Operating Temperature": "-"}, -40) is None
    # A single figure states a ceiling, not a floor.
    assert payload.rated_from({"Operating Temperature": "125℃"}, -40) is None


def test_a_cold_end_constraint_excludes_the_part_that_just_failed():
    """Why the repair loop could not escape an outdoor brief.

    R7 detected the -40 °C violation, but nothing could act on it: the re-search had no
    way to exclude the part, so the same one came back and the run escalated with a
    question the user could not answer either.
    """
    from continuity.graph.sourcing import viable

    warm = _candidate("WARM", **{"Operating Temperature": "-25℃~+85℃"})
    cold = _candidate("COLD", **{"Operating Temperature": "-40℃~+125℃"})

    survivors = viable([warm, cold], {"rated_from": -40})

    assert [c.mpn for c in survivors] == ["COLD"]


# ── bundled paths do not depend on where the process was started ──────────────


def test_bundled_directories_are_anchored_to_the_package():
    """`Path("cache/normalized")` resolves against the working directory.

    On a laptop that is `backend/`, because that is where the server is started. In a
    container image it is usually `/app` or `/`, and then the committed parse cache is
    simply not there: nothing fails, every part is fetched and parsed again, and an empty
    directory appears wherever the process happened to start.
    """
    from continuity.parts import datasheet, fixtures, normalize

    for path in (normalize.CACHE_DIR, datasheet.CACHE_DIR, fixtures.FIXTURE_DIR):
        assert path.is_absolute(), f"{path} would move with the working directory"


def test_a_deployment_may_point_the_cache_at_a_volume(monkeypatch):
    """Where a mounted volume exists, the deployment names it; where none does, the
    committed cache is still found and what the run adds is lost on restart."""
    from continuity import env

    monkeypatch.delenv("CONTINUITY_CACHE_DIR", raising=False)
    assert env.cache_dir("normalized") == env.ROOT / "cache" / "normalized"

    monkeypatch.setenv("CONTINUITY_CACHE_DIR", "/mnt/data/cache")
    assert env.cache_dir("normalized") == Path("/mnt/data/cache/normalized")
