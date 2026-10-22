"""Reference circuits for numerical, topology, and repair regressions."""
import asyncio
from dataclasses import replace

import pytest

from fuze.designs import service
from fuze.engine import draw, policy, repairs, rules
from fuze.engine.models import Board, Port, Rail, Requirements, SignalNet, Verdict
from fuze.graph import sourcing
from fuze.parts.search import Candidate
from tests import parts
from tests.boards import slot, usb_board


def checks(board, rule, scope=None):
    return [v for v in service.evaluate(board) if v.rule == rule and (scope is None or v.scope == scope)]


@pytest.mark.parametrize("field,value", [("i_max", float("nan")), ("i_max", -1),
    ("efficiency", 0), ("efficiency", 1.2), ("vmax", float("inf")), ("theta_ja", -5),
    ("pins_available", True), ("i_peak", "unknown")])
def test_invalid_measurements_are_explicitly_excluded(field, value):
    board = usb_board(regulator=parts.buck_3v3(**{field: value}), loads={"mcu": parts.esp32s3()})
    result = checks(board, "data_integrity")
    assert result and all(v.unchecked for v in result)
    assert field in result[0].detail
    assert getattr(board.part("regulator"), field) is value


def test_inverted_operating_range_cannot_be_a_pass():
    board = usb_board(regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3(vmin=5, vmax=2)})
    assert any(v.unchecked for v in checks(board, "data_integrity"))
    assert next(v for v in checks(board, "voltage_overlap", "3V3") if v.subject == "mcu").unchecked


@pytest.mark.parametrize("limit,load,expected", [(0, 0, "pass"), (0, 0.01, "fail")])
def test_zero_current_ceiling_is_evaluated_without_division_by_zero(limit, load, expected):
    board = Board(Requirements(), {"sensor": slot("sensor", parts.sht31(i_typ=None, i_peak=load))},
                  {"input": Rail("input", 3.3, members=("sensor",), i_limit=limit)})
    assert checks(board, "current_budget")[0].status == expected


def test_partial_downstream_load_still_proves_upstream_overload():
    board = usb_board(regulator=parts.ap2112k(i_max=2), loads={
        "mcu": parts.esp32s3(i_peak=0.5), "sensor": parts.sht31(i_peak=None, i_typ=None)})
    board = replace(board, rails={**board.rails, "5V0": replace(board.rails["5V0"], i_limit=0.2)})
    verdict = checks(board, "current_budget", "5V0")[0]
    assert verdict.failed and "floor" in verdict.detail
    assert "500 mA" in verdict.detail


def test_all_output_rails_contribute_to_input_current():
    board = usb_board(regulator=parts.ap2112k(i_max=2), loads={"mcu": parts.esp32s3(i_peak=0.4)})
    board = replace(board, slots={**board.slots, "sensor": slot("sensor", parts.sht31(i_peak=0.3))},
                    rails={**board.rails, "aux": Rail("aux", 3.3, "regulator", ("sensor",)),
                           "5V0": replace(board.rails["5V0"], i_limit=0.6)})
    estimate, missing = draw.rail_draw(board, draw.consumers(board, board.rails["5V0"]))
    assert estimate == pytest.approx(0.7) and not missing
    assert checks(board, "current_budget", "5V0")[0].failed


def test_unknown_regulator_type_does_not_assume_linear_current():
    board = usb_board(regulator=parts.ap2112k(topology=None, category="Unknown"), loads={"mcu": parts.esp32s3()})
    assert checks(board, "current_budget", "5V0")[0].unchecked


def test_assumed_efficiency_cannot_prove_an_overload_that_an_ideal_converter_avoids():
    board = usb_board(regulator=parts.buck_3v3(efficiency=None),
                      loads={"mcu": parts.esp32s3(i_peak=1.0)}, requirements=Requirements(current_margin=0))
    board = replace(board, rails={**board.rails, "5V0": replace(board.rails["5V0"], i_limit=0.75)})
    # 3.3 W / 5 V = 660 mA ideal; the old 80% estimate alone claimed 825 mA as a failure.
    assert checks(board, "current_budget", "5V0")[0].unchecked


def test_overload_even_at_unity_efficiency_is_definite():
    board = usb_board(regulator=parts.buck_3v3(efficiency=None),
                      loads={"mcu": parts.esp32s3(i_peak=1.0)}, requirements=Requirements(current_margin=0))
    board = replace(board, rails={**board.rails, "5V0": replace(board.rails["5V0"], i_limit=0.5)})
    assert checks(board, "current_budget", "5V0")[0].failed


def test_invalid_requirement_cannot_silently_relax_all_budgets():
    board = usb_board(regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3()},
                      requirements=Requirements(current_margin=float("nan")))
    assert all(v.unchecked for v in service.evaluate(board))


def test_unknown_load_produces_an_explicit_thermal_finding():
    board = usb_board(regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3(i_peak=None, i_typ=None)})
    assert checks(board, "thermal_dissipation", "3V3")[0].unchecked


def test_typical_only_load_makes_upstream_worst_case_unknown():
    board = usb_board(regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3(i_peak=None, i_typ=0.05)})
    assert checks(board, "current_budget", "5V0")[0].unchecked


@pytest.mark.parametrize("topology,input_voltage,output_voltage", [("buck", 3, 5), ("boost", 5, 3.3)])
def test_converter_direction_is_checked(topology, input_voltage, output_voltage):
    board = usb_board(regulator=parts.buck_3v3(topology=topology, vout_min=1, vout_max=12),
                      loads={"mcu": parts.esp32s3()}, input_voltage=input_voltage, rail_voltage=output_voltage)
    verdict = next(v for v in checks(board, "voltage_overlap", "3V3") if v.subject == "regulator")
    assert verdict.failed and "cannot" in verdict.detail


def test_duplicate_rail_members_do_not_double_count_current():
    board = usb_board(regulator=parts.ap2112k(), loads={"sensor": parts.sht31(i_peak=0.01)})
    board = replace(board, rails={**board.rails, "3V3": replace(board.rails["3V3"], members=("sensor", "sensor"))})
    assert checks(board, "power_topology")
    verdict = checks(board, "current_budget", "3V3")[0]
    assert verdict.unchecked and "10 mA" in verdict.detail


def test_ambiguous_supply_is_not_chosen_by_dictionary_order():
    board = usb_board(regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3()})
    board = replace(board, rails={**board.rails, "other": Rail("other", 12, members=("regulator",), i_limit=1)})
    assert board.input_rail("regulator") is None
    assert checks(board, "power_topology")
    assert not any(v.status == "pass" for v in checks(board, "thermal_dissipation"))


def test_power_cycle_terminates_and_cannot_pass_its_current_budget():
    board = Board(Requirements(), {"a": slot("a", parts.ap2112k()), "b": slot("b", parts.ap2112k())},
                  {"ra": Rail("ra", 3.3, "a", ("b",)), "rb": Rail("rb", 3.3, "b", ("a",))})
    assert any("cycle" in v.detail for v in checks(board, "power_topology"))
    assert all(v.unchecked for v in checks(board, "current_budget"))


def signal_board(*nets, capacity=4):
    return Board(Requirements(), {
        "mcu": slot("mcu", parts.esp32s3(pins_available=capacity)),
        "sensor": slot("sensor", parts.sht31()), "display": slot("display", parts.sht31()),
    }, {}, nets)


def bus(name, *members, protocol="I2C", basis="confirmed"):
    return SignalNet(name, protocol, tuple(Port(key, name, role) for key, role in members), basis)


def test_peripherals_on_a_confirmed_net_need_a_controller():
    board = signal_board(bus("i2c", ("sensor", "peripheral"), ("display", "peripheral")))
    assert all(v.failed for v in checks(board, "interface_role_match", "i2c"))


def test_peripheral_cannot_become_a_controller_by_confirming_a_net():
    board = signal_board(bus("i2c", ("sensor", "controller"), ("display", "peripheral")))
    assert next(v for v in checks(board, "interface_role_match", "i2c") if v.subject == "sensor").failed


def test_unconfirmed_connections_remain_unknown():
    board = signal_board(bus("i2c", ("sensor", "controller"), ("display", "peripheral"), basis="inferred"))
    assert all(v.unchecked for v in checks(board, "interface_role_match", "i2c"))


def test_independent_i2c_buses_each_consume_two_pins():
    board = signal_board(bus("a", ("mcu", "controller"), ("sensor", "peripheral")),
                         bus("b", ("mcu", "controller"), ("display", "peripheral")), capacity=3)
    verdict = checks(board, "pin_budget")[0]
    assert verdict.failed and "4 GPIO" in verdict.detail


def test_shared_i2c_bus_only_consumes_two_pins():
    board = signal_board(bus("shared", ("mcu", "controller"), ("sensor", "peripheral"), ("display", "peripheral")), capacity=2)
    verdict = checks(board, "pin_budget")[0]
    assert verdict.status == "pass" and "2 GPIO" in verdict.detail


def test_reference_board_report_survives_round_trip_with_new_checks():
    board = signal_board(bus("i2c", ("sensor", "peripheral"), ("display", "peripheral")))
    document = service.document(board)
    assert service.document(service.board_from_document(document))["report"] == document["report"]
    ids = [v["id"] for v in document["report"]["findings"]]
    assert len(ids) == len(set(ids))


def test_thermal_repair_uses_required_rail_and_load_not_a_fixed_amp():
    board = usb_board(regulator=parts.ap2112k(vout_min=1, vout_max=5, i_max=3),
                      loads={"mcu": parts.esp32s3(i_peak=1.5)}, rail_voltage=3.3)
    conflict = Verdict("thermal_dissipation", "fail", "hot", "regulator", scope="3V3")
    resolution = policy.Resolution(conflict, ("regulator",), False)
    repair = repairs.propose(board, resolution, {})
    assert repair.action == "change_topology"
    assert repair.constraint["vout"] == 3.3
    assert repair.constraint["i_out_min"] == pytest.approx(1.725)
    assert "efficiency_min" not in repair.constraint
    assert policy.enforce(repair, resolution, board).accepted


def test_temperature_repair_preserves_both_operating_limits():
    board = usb_board(regulator=parts.ap2112k(), loads={"sensor": parts.sht31()},
                      requirements=Requirements(temp_range=(-40, 105)))
    conflict = Verdict("temperature_rating", "fail", "too cold", "sensor")
    repair = repairs.propose(board, policy.Resolution(conflict, ("sensor",), False), {})
    assert repair.constraint == {"rated_from": -40, "rated_to": 105}


def test_candidate_comparison_prefers_the_part_that_passes_over_a_cheaper_failure(monkeypatch):
    board = usb_board(regulator=parts.ap2112k(), loads={"sensor": parts.sht31()})
    candidates = sourcing.SearchResults([
        Candidate(lcsc="C1", mpn="bad", manufacturer="Test", description="bad", package="SOT-23", category="LDO", subcategory="LDO", library_type="basic", stock=1000, unit_price=0.01),
        Candidate(lcsc="C2", mpn="good", manufacturer="Test", description="good", package="SOT-23", category="LDO", subcategory="LDO", library_type="basic", stock=1000, unit_price=1),
    ])
    async def choose(candidate):
        return parts.ap2112k(mpn=candidate.mpn, vout_min=5 if candidate.mpn == "bad" else 3.3,
                             vout_max=5 if candidate.mpn == "bad" else 3.3, unit_price=candidate.unit_price)
    monkeypatch.setattr(sourcing, "choose", choose)
    selected, _, comparisons = asyncio.run(sourcing.compare_candidates(candidates, board, "regulator"))
    assert selected.mpn == "good"
    assert comparisons[0]["new_failures"] == 0


def test_quantity_survives_revision_round_trip_and_is_cited():
    board = usb_board(regulator=parts.ap2112k(), loads={"sensor": parts.sht31(i_peak=0.4)})
    board = replace(board, slots={**board.slots, "sensor": replace(board.slots["sensor"], quantity=2)})
    document = service.document(board, origin="bom")
    restored = service.board_from_document(document)
    verdict = checks(restored, "current_budget", "3V3")[0]
    assert verdict.failed and "800 mA" in verdict.detail
    assert any(e.field == "quantity" and e.value == "2" for e in verdict.evidence)
    assert document["bom"]["rows"][-1]["qty"] == 2
    assert service.document(restored, origin="bom")["report"] == document["report"]


def test_legacy_bom_quantities_are_migrated_without_double_counting():
    board = usb_board(regulator=parts.ap2112k(), loads={"sensor": parts.sht31(i_peak=0.4)})
    document = service.document(board, origin="bom", quantities={"sensor": 2})
    for value in document["board"]["slots"].values():
        value.pop("quantity")
    document["board"]["rails"]["3V3"]["members"] = ["sensor", "sensor"]
    restored = service.board_from_document(document)
    assert restored.slots["sensor"].quantity == 2
    assert restored.rails["3V3"].members == ("sensor",)
    total, missing = draw.rail_draw(restored, draw.consumers(restored, restored.rails["3V3"]))
    assert total == pytest.approx(0.8) and not missing


@pytest.mark.parametrize("field,value", [("current_margin", None), ("max_lead_days", None), ("ambient_c", float("nan"))])
def test_invalid_required_inputs_cannot_crash_or_pass(field, value):
    board = usb_board(regulator=parts.ap2112k(), loads={"sensor": parts.sht31()},
                      requirements=replace(Requirements(), **{field: value}))
    assert all(v.unchecked for v in service.evaluate(board))


def test_multiple_controllers_require_explicit_pin_assignments():
    board = signal_board()
    board = replace(board, slots={**board.slots, "second": slot("second", parts.esp32s3())})
    verdicts = checks(board, "pin_budget")
    assert {v.subject for v in verdicts} == {"mcu", "second"}
    assert all(v.unchecked for v in verdicts)


def test_repair_search_never_reselects_an_already_failed_component(monkeypatch):
    from types import SimpleNamespace
    from fuze.api.events import EventStream
    from fuze.graph import nodes
    board = usb_board(regulator=parts.ap2112k(), loads={"sensor": parts.sht31()})
    part = board.part("regulator")
    same = Candidate(lcsc="C1", mpn=part.mpn, manufacturer=part.manufacturer, description="same part",
        category="LDO", subcategory="LDO", package=part.package, library_type="basic", stock=1000, unit_price=1)
    async def find(*args, **kwargs):
        return [same]
    async def choose(candidate):
        pytest.fail("An already failed component must not consume another normalisation request")
    monkeypatch.setattr(sourcing, "find", find)
    monkeypatch.setattr(sourcing, "choose", choose)
    monkeypatch.setattr(nodes, "_emit", lambda event: None)
    state = {"requirements": board.requirements, "slots": board.slots, "rails": board.rails,
             "current": "regulator", "constraint": {"i_out_min": 2},
             "plan": SimpleNamespace(queries={"regulator": "LDO regulator"})}
    result = asyncio.run(nodes.apply(state, {"configurable": {"events": EventStream("repeat-test")}}))
    assert "No untried component" in result["escalation"]
