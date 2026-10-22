"""Independent boundary cases for source verification and reproducible designs."""
import asyncio
import json
from dataclasses import asdict, replace

import pytest

from continuity.parts import normalize, specifications, datasheet
from continuity.engine.models import Board, Requirements, Slot, Rail, PartSpec, Verdict, SignalNet, Port
from continuity.designs import service


def sensor_board(maximum=3.6, voltage=5.0):
    part = PartSpec("REFERENCE-SENSOR", "Reference", "Reference fixture", "sensor", vmin=1.8, vmax=maximum, interfaces=("I2C",), role="peripheral", i_peak=0.01, stock=1000, unit_price=2)
    return Board(Requirements(), {"sensor": Slot("sensor", "Sensor", "peripherals", part=part)},
                 {"input": Rail("input", voltage, members=("sensor",), i_limit=1, basis="User supplied")})


@pytest.mark.parametrize("reply", [
    {"vmax": 1000}, {"vmax": 1000, "provenance": {"vmax": "Voltage - Supply"}},
    {"vmax": 1000, "provenance": ["malformed"]},
])
def test_source_citation_cannot_launder_a_fabricated_number(reply):
    parsed, _ = normalize.validate(reply, {"Voltage - Supply": "1.8V~3.6V"})
    assert "vmax" not in parsed


@pytest.mark.parametrize("source", ["1.8V~3.6V", "1800mV~3600mV", "1.8~3.6V"])
def test_unit_variants_preserve_the_independent_voltage_verdict(source):
    values, provenance = specifications.parse({"Voltage - Supply": source})
    assert values == {"vmin": 1.8, "vmax": 3.6}
    board = sensor_board(values["vmax"])
    result = next(v for v in service.evaluate(board) if v.rule == "voltage_overlap")
    assert result.status == "fail"
    assert provenance["vmax"] == "Voltage - Supply"


@pytest.mark.parametrize(("supply", "expected"), [(1.0, "FAIL"), (1.8, "PASS"), (3.3, "PASS"), (3.6, "PASS"), (5, "FAIL")])
def test_reference_voltage_cases(supply, expected):
    report = service.document(sensor_board(voltage=supply))["report"]
    assert next(f["outcome"] for f in report["findings"] if f["rule"] == "voltage_overlap") == expected


def test_unknown_voltage_is_not_pass():
    board = sensor_board()
    board = replace(board, slots={"sensor": replace(board.slots["sensor"], part=replace(board.part("sensor"), vmin=None, vmax=None))})
    report = service.document(board)["report"]
    assert next(f["outcome"] for f in report["findings"] if f["rule"] == "voltage_overlap") == "UNKNOWN"


def test_documents_round_trip_and_reproduce_verdicts_without_network():
    original = service.document(sensor_board(), quantities={"sensor": 3})
    serialized = json.loads(json.dumps(original))
    again = service.document(service.board_from_document(serialized), quantities=serialized["quantities"])
    assert again["report"] == original["report"]
    assert again["bom"]["total"] == 6
    assert again["bom"]["rows"][0]["qty"] == 3


def test_waivers_keep_the_original_failure_in_reports():
    verdict = Verdict("voltage_overlap", "warn", "Accepted condition", "sensor", original_status="fail", accepted=True)
    report = service.document(sensor_board(), verdicts=[verdict])["report"]
    assert report["findings"][0]["outcome"] == "FAIL"
    assert report["findings"][0]["accepted"] is True
    assert report["counts"]["fail"] == 1


def test_new_waivers_do_not_cover_changed_evidence_or_another_rail():
    from continuity.graph.nodes import _apply_waivers, _waiver_key
    original = Verdict("voltage_overlap", "fail", "5 V exceeds 3.6 V", "sensor", scope="input")
    changed = replace(original, detail="12 V exceeds 3.6 V")
    other_rail = replace(original, scope="other")
    result = _apply_waivers([original, changed, other_rail], [_waiver_key(original)])
    assert [item.accepted for item in result] == [True, False, False]
    assert [item.status for item in result] == ["warn", "fail", "fail"]


def test_confirmed_nets_do_not_conflate_separate_controllers():
    board = sensor_board(voltage=3.3)
    master = replace(board.part("sensor"), role="master")
    slots = {**board.slots, "a": Slot("a", "A", "core", part=master), "b": Slot("b", "B", "core", part=master)}
    net = SignalNet("sensor-bus", "I2C", (Port("a", "I2C", "controller"), Port("sensor", "I2C", "peripheral")), "confirmed")
    result = service.evaluate(replace(board, slots=slots, signal_nets=(net,)))
    assert not [v for v in result if v.rule == "interface_role_match" and v.status == "fail"]


def test_thermal_quote_must_contain_the_claimed_number():
    text = "RθJA Junction-to-ambient thermal resistance 116.3 °C/W"
    assert datasheet._fact_from_reply({"theta_ja": 20, "source_line": text}, text, "SOIC-8") is None
    assert datasheet._fact_from_reply({"theta_ja": 116.3, "source_line": text}, text, "SOIC-8").theta_ja == 116.3


def test_shortlist_rejects_a_cheaper_incompatible_part(monkeypatch):
    from continuity.graph import sourcing
    from tests.test_normalize import CANDIDATE
    bad = replace(CANDIDATE, mpn="CHEAP-BAD")
    good = replace(CANDIDATE, mpn="SAFE")
    async def choose(candidate):
        return replace(sensor_board().part("sensor"), mpn=candidate.mpn,
                       vmax=3.6 if candidate is bad else 6.0,
                       unit_price=0.01 if candidate is bad else 5.0)
    monkeypatch.setattr(sourcing, "choose", choose)
    candidate, part, comparison = asyncio.run(sourcing.compare_candidates(
        sourcing.SearchResults([bad, good]), sensor_board(voltage=5), "sensor"))
    assert candidate is good and part.mpn == "SAFE"
    assert comparison[0]["failures"] < comparison[1]["failures"]
