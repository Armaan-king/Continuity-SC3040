"""Power tree assembly, and the graph derived from it."""

from __future__ import annotations

import pytest

from continuity.engine import rules
from continuity.engine.models import Board
from continuity.planner import topology
from continuity.planner.topology import INPUT_SOURCES
from continuity.engine.models import Rail, Requirements, Slot
from tests import parts


def slot(slot_id, part=None, **kw):
    return Slot(id=slot_id, label=slot_id, tier=kw.pop("tier", "core"), part=part, **kw)


def demo_slots():
    return {
        "regulator": slot("regulator", parts.ldo_600ma(), tier="power"),
        "mcu": slot("mcu", parts.esp32s3(), pinned=True),
        "sensor": slot("sensor", parts.sht31(), tier="peripherals", pinned=True),
        "display": slot("display", parts.oled(i_peak=0.040), tier="peripherals", pinned=True),
    }


DEMO_RAIL = Rail("3V3", 3.3, "regulator", ("mcu", "sensor", "display"))


# ── the input rail the planner cannot know about ──────────────────────────────


def test_the_board_input_rail_is_synthesised_from_the_brief():
    rails = topology.assemble_rails([DEMO_RAIL], Requirements(input_source="usb-5v"))

    vin = rails[topology.INPUT_RAIL_ID]
    assert vin.voltage == 5.0
    assert vin.i_limit == 1.5
    assert vin.source is None
    assert vin.members == ("regulator",)


def test_an_unknown_input_source_is_refused_rather_than_guessed():
    """Guessing here inverts verdicts — see `UnknownPowerSource`."""
    with pytest.raises(topology.UnknownPowerSource) as raised:
        topology.assemble_rails([DEMO_RAIL], Requirements(input_source="mystery"))

    assert "cannot be assumed" in str(raised.value)
    assert "usb-5v" in str(raised.value), "the message should list what it does know"


def test_a_higher_input_voltage_fails_a_regulator_that_cannot_take_it():
    """The bug this replaced: 12 V modelled as 5 V made R1 pass a 6 V-max part."""
    slots = {
        "regulator": slot("regulator", parts.ap2112k(), tier="power"),  # 2.5–6.0 V
        "mcu": slot("mcu", parts.esp32s3(), pinned=True),
    }
    rail = Rail("3V3", 3.3, "regulator", ("mcu",))

    usb = topology.build_board(slots, [rail], Requirements(input_source="usb-5v"))
    barrel = topology.build_board(slots, [rail], Requirements(input_source="12v-barrel"))

    def supply(board):
        # The regulator now earns two verdicts: one as a *member* of the rail feeding it
        # (this one, unscoped) and one as the *source* of the rail it makes (scoped to
        # that rail). This test is about what it can be fed.
        return next(
            v
            for v in rules.voltage_overlap(board)
            if v.subject == "regulator" and v.scope == topology.INPUT_RAIL_ID
        )

    assert supply(usb).status == "pass"
    assert supply(barrel).status == "fail"
    assert "VIN at 12 V is above that" in supply(barrel).detail


def test_a_coin_cell_cannot_supply_a_radio():
    """A CR2032 pulses to ~20 mA. Rating it higher passes boards that flatten it."""
    slots = {
        "regulator": slot("regulator", parts.buck_3v3(), tier="power"),
        "mcu": slot("mcu", parts.esp32s3(), pinned=True),
    }
    board = topology.build_board(
        slots, [Rail("3V3", 3.3, "regulator", ("mcu",))], Requirements(input_source="battery-3v0")
    )

    budget = next(
        v
        for v in rules.current_budget(board)
        if v.scope == topology.INPUT_RAIL_ID
    )

    assert budget.status == "fail"


def test_a_dual_supply_is_sized_against_the_higher_voltage():
    """A linear regulator dissipates worst on USB; sizing against the battery passes a
    board that cooks the moment it is plugged in."""
    rails = topology.assemble_rails([DEMO_RAIL], Requirements(input_source="usb-5v+liion"))

    assert rails[topology.INPUT_RAIL_ID].voltage == 5.0


def test_a_cascade_does_not_attach_every_regulator_to_the_input():
    """12 V → 5 V → 3V3. The 5 V regulator already has an upstream rail."""
    declared = [
        Rail("5V", 5.0, "r1", ("r2",)),
        Rail("3V3", 3.3, "r2", ("mcu",)),
    ]

    rails = topology.assemble_rails(declared, Requirements())

    assert rails[topology.INPUT_RAIL_ID].members == ("r1",)


def test_a_direct_drive_board_attaches_every_slot_to_the_input_rail():
    """Coin-cell BLE boards have no regulator, but VIN must still feed every part."""
    slots = demo_slots()
    board = topology.build_board(slots, [], Requirements(input_source="battery-3v0"))

    assert board.rails[topology.INPUT_RAIL_ID].members == tuple(slots)
    assert [verdict.scope for verdict in rules.current_budget(board)] == [topology.INPUT_RAIL_ID]
    # Every one of them draws from the supply. When this returned no edges at all, a
    # coin-cell board rendered as a field of unconnected boxes.
    assert [(e.source, e.target) for e in topology.power_edges(board.rails)] == [
        (topology.SUPPLY_NODE_ID, slot_id) for slot_id in slots
    ]


def test_a_slot_on_no_rail_draws_an_unchecked_stub_and_joins_no_budget():
    """The solar board's charge controller: a member of nothing, a source of nothing.

    It must not float on screen, and it must not be quietly adopted onto the input rail
    either — its supply is the panel, and a 3.7 V battery rail would fail it on R1.
    """
    slots = {"buck": None, "mcu": None, "charger": None, "battery": None}
    declared = [Rail("3V3", 3.3, "buck", ("mcu",))]

    rails = topology.assemble_rails(declared, Requirements(), slot_ids=tuple(slots))
    stubs = topology.unmodelled(slots, rails)

    assert rails[topology.INPUT_RAIL_ID].members == ("buck",)
    assert [(e.target, e.source, e.label, e.status) for e in stubs] == [
        ("charger", topology.SUPPLY_NODE_ID, None, topology.UNCHECKED),
        ("battery", topology.SUPPLY_NODE_ID, None, topology.UNCHECKED),
    ]


def test_a_clean_board_does_not_upgrade_an_unchecked_stub_to_pass():
    """No rule ran on it, so no outcome may claim one did."""
    slots = demo_slots() | {"antenna": slot("antenna", tier="peripherals")}
    board = topology.build_board(slots, [DEMO_RAIL], Requirements())

    resolved = {e.target: e.status for e in topology.resolved_edges(board, [])}

    assert resolved["antenna"] == topology.UNCHECKED
    assert resolved["mcu"] == "pass"


def test_the_input_rail_makes_thermal_computable():
    """Without it R5 has no Vin and goes silent — see the module docstring."""
    slots = demo_slots() | {"regulator": slot("regulator", parts.ldo_1a(), tier="power")}

    without = rules.thermal_dissipation(
        Board(Requirements(), slots, {"3V3": DEMO_RAIL})
    )
    with_input = rules.thermal_dissipation(
        topology.build_board(slots, [DEMO_RAIL], Requirements())
    )

    assert without[0].status == "warn"
    assert "no known input rail" in without[0].detail
    assert with_input[0].status == "fail"


# ── the grouping that edges cannot express ────────────────────────────────────


def test_members_of_one_rail_are_budgeted_together():
    """Three parts on a net draw the sum. Treated pairwise, each passes alone."""
    board = topology.build_board(demo_slots(), [DEMO_RAIL], Requirements())

    budget = [v for v in rules.evaluate(board) if v.rule == "current_budget" and v.scope == "3V3"]

    assert budget[0].status == "fail"
    assert "541.5 mA" in budget[0].detail


# ── edges, derived ────────────────────────────────────────────────────────────


def test_one_power_edge_per_rail_member():
    board = topology.build_board(demo_slots(), [DEMO_RAIL], Requirements())

    edges = topology.power_edges(board.rails)

    assert [(e.source, e.target, e.label) for e in edges] == [
        ("regulator", "mcu", "3V3"),
        ("regulator", "sensor", "3V3"),
        ("regulator", "display", "3V3"),
        (topology.SUPPLY_NODE_ID, "regulator", "VIN"),
    ]
    assert [e.id for e in edges] == ["pwr-mcu", "pwr-sensor", "pwr-display", "pwr-regulator"]


def test_power_edges_dedupe_members_first_rail_wins():
    rails = {
        "3V3": Rail("3V3", 3.3, "reg3", ("mcu",)),
        "1V8": Rail("1V8", 1.8, "reg1", ("mcu",)),
    }

    assert topology.power_edges(rails) == [
        topology.Edge("pwr-mcu", "reg3", "mcu", "3V3", "power", "pending")
    ]


def test_the_input_rail_draws_from_the_supply_node():
    """It has no source *slot*, so it draws from the presentation node instead."""
    slots = demo_slots() | {"regulator": slot("regulator", parts.ldo_1a(), tier="power")}
    board = topology.build_board(slots, [DEMO_RAIL], Requirements())

    from_input = [e for e in topology.power_edges(board.rails) if e.label == topology.INPUT_RAIL_ID]

    assert [(e.id, e.source, e.target) for e in from_input] == [
        ("pwr-regulator", topology.SUPPLY_NODE_ID, "regulator")
    ]


def test_the_supply_node_is_never_a_slot():
    """`plan.slots` carries "every declared slot ends resolved"; the supply resolves nothing."""
    slots = demo_slots() | {"regulator": slot("regulator", parts.ldo_1a(), tier="power")}
    board = topology.build_board(slots, [DEMO_RAIL], Requirements())

    assert topology.SUPPLY_NODE_ID not in board.slots
    assert all(e.target != topology.SUPPLY_NODE_ID for e in topology.graph_edges(board))


def test_a_cascade_hangs_only_its_topmost_regulator_off_the_supply():
    """12 V → 5 V → 3V3 still reads left to right, with one edge from the input."""
    declared = [Rail("5V", 5.0, "r1", ("r2",)), Rail("3V3", 3.3, "r2", ("mcu",))]

    edges = topology.power_edges(topology.assemble_rails(declared, Requirements()))

    assert [(e.source, e.target, e.label) for e in edges] == [
        ("r1", "r2", "5V"),
        ("r2", "mcu", "3V3"),
        (topology.SUPPLY_NODE_ID, "r1", "VIN"),
    ]


def test_power_edge_labels_are_never_null():
    """The rail voltage is a design decision made before any part is chosen."""
    board = topology.build_board(demo_slots(), [DEMO_RAIL], Requirements())

    power = [e for e in topology.graph_edges(board) if e.kind == "power"]

    assert power and all(e.label for e in power)


def test_edge_ids_are_stable_so_selection_can_patch_them():
    board = topology.build_board(demo_slots(), [DEMO_RAIL], Requirements())

    first = [e.id for e in topology.graph_edges(board)]
    second = [e.id for e in topology.graph_edges(board)]

    assert first == second
    assert len(set(first)) == len(first), "ids must be unique to patch by id"


def test_data_edges_run_master_to_peripheral_and_name_the_bus():
    board = topology.build_board(demo_slots(), [DEMO_RAIL], Requirements())

    data = [e for e in topology.graph_edges(board) if e.kind == "data"]

    assert {(e.source, e.target, e.label) for e in data} == {
        ("mcu", "sensor", "I2C"),
        ("mcu", "display", "I2C"),
    }


def test_a_data_edge_with_no_shared_bus_stays_unlabelled():
    slots = demo_slots()
    slots["sensor"] = slot("sensor", parts.sht31(interfaces=("SPI",)), tier="peripherals")
    slots["mcu"] = slot("mcu", parts.esp32s3(interfaces=("I2C",)))
    board = topology.build_board(slots, [DEMO_RAIL], Requirements())

    sensor_edge = next(
        e for e in topology.graph_edges(board) if e.target == "sensor" and e.kind == "data"
    )

    assert sensor_edge.label is None


def test_data_edges_use_the_master_that_offers_each_peripherals_bus():
    slots = {
        "i2c_mcu": slot("i2c_mcu", parts.esp32s3(interfaces=("I2C",))),
        "spi_mcu": slot("spi_mcu", parts.esp32s3(interfaces=("SPI",))),
        "sensor": slot("sensor", parts.sht31(), tier="peripherals"),
        "flash": slot("flash", parts.spi_flash(), tier="peripherals"),
    }

    data = topology.data_edges(slots)

    assert {(edge.source, edge.target, edge.label) for edge in data} == {
        ("i2c_mcu", "sensor", "I2C"),
        ("spi_mcu", "flash", "SPI"),
    }
    assert len({edge.id for edge in data}) == len(data)


def test_planned_data_edges_keep_the_explicit_master_and_stable_ids():
    edges = topology.planned_data_edges(
        (("i2c_mcu", "sensor"), ("spi_mcu", "flash"), ("spi_mcu", "flash"))
    )

    assert [(edge.source, edge.target) for edge in edges] == [
        ("i2c_mcu", "sensor"),
        ("spi_mcu", "flash"),
    ]
    assert len({edge.id for edge in edges}) == len(edges)


def test_no_controller_means_no_data_edges():
    slots = {"sensor": slot("sensor", parts.sht31(), tier="peripherals")}

    assert topology.data_edges(slots) == []


# ── status, patched from verdicts ─────────────────────────────────────────────


def test_a_failing_rail_marks_its_edges_conflict():
    board = topology.build_board(demo_slots(), [DEMO_RAIL], Requirements())

    edges = {e.id: e for e in topology.resolved_edges(board, rules.evaluate(board))}

    assert edges["pwr-mcu"].status == "conflict"
    assert edges["pwr-display"].status == "conflict"


def test_a_clean_board_marks_every_edge_pass():
    slots = demo_slots() | {"regulator": slot("regulator", parts.buck_3v3(), tier="power")}
    board = topology.build_board(slots, [DEMO_RAIL], Requirements())

    edges = topology.resolved_edges(board, rules.evaluate(board))

    assert {e.status for e in edges} == {"pass"}


# ── what the model may decide, and what it may not ────────────────────────────


def test_a_proposed_supply_is_marked_as_inferred():
    """The planner may read a supply off the brief — it arrives flagged, not trusted."""
    proposed = topology.proposed_source(7.4, 2.0, "2x 18650 in series")

    assert proposed.is_inferred
    assert not topology.INPUT_SOURCES["usb-5v"].is_inferred


def test_a_proposed_supply_can_be_used_once_confirmed():
    """The vocabulary is 11 entries; boards are not. The seam has to exist."""
    board = topology.build_board(
        demo_slots(),
        [DEMO_RAIL],
        Requirements(input_source="two-18650s"),
        supply=topology.proposed_source(7.4, 2.0, "2x 18650 in series"),
    )

    assert board.rails[topology.INPUT_RAIL_ID].voltage == 7.4


def test_every_table_entry_states_where_its_numbers_came_from():
    assert all(s.basis for s in topology.INPUT_SOURCES.values())


def test_an_inferred_supply_is_cited_in_the_verdicts_that_rest_on_it():
    """A guessed 6 V must not appear on screen as confidently as a spec figure."""
    slots = {
        "regulator": slot("regulator", parts.ap2112k(), tier="power"),
        "mcu": slot("mcu", parts.esp32s3(), pinned=True),
    }
    board = topology.build_board(
        slots,
        [Rail("3V3", 3.3, "regulator", ("mcu",))],
        Requirements(input_source="6v-solar"),
        supply=topology.proposed_source(6.0, 0.5, "6V solar panel"),
    )

    thermal = next(v for v in rules.thermal_dissipation(board) if v.subject == "regulator")
    cited = {e.field: e.source for e in thermal.evidence}

    assert cited["VIN supply voltage"] == topology.INFERRED_BASIS
    assert "not a standard supply" in cited["VIN supply voltage"]


def test_a_standard_supply_cites_the_specification_instead():
    slots = {
        "regulator": slot("regulator", parts.ap2112k(), tier="power"),
        "mcu": slot("mcu", parts.esp32s3(), pinned=True),
    }
    board = topology.build_board(
        slots, [Rail("3V3", 3.3, "regulator", ("mcu",))], Requirements(input_source="usb-5v")
    )

    thermal = next(v for v in rules.thermal_dissipation(board) if v.subject == "regulator")
    cited = {e.field: e.source for e in thermal.evidence}

    assert cited["VIN supply voltage"] == "USB Type-C default Rp advertisement"


def test_a_regulator_fed_rail_does_not_invent_a_supply_citation():
    """Its voltage comes from the regulator's datasheet; the verdict already quotes it."""
    board = topology.build_board(demo_slots(), [DEMO_RAIL], Requirements())

    on_3v3 = next(v for v in rules.voltage_overlap(board) if v.subject == "mcu")

    assert all("supply voltage" not in e.field for e in on_3v3.evidence)


# ── a voltage the user stated outright ────────────────────────────────────────
#
# Running §2 on 9 Aug, two of three briefs came out wrong by exactly 2x:
#
#   "sensor node running off a 48V industrial bus"      -> 24 V  (24v-industrial)
#   "something powered by two 18650 cells in series"    -> 3.7 V (battery-3v7)
#
# Neither classification is unreasonable — there is no 48 V industrial entry and no
# multi-cell entry, so the model picked the nearest thing it could name. The vocabulary
# is lossy and nothing detected the loss. A voltage the user wrote down is sourced data,
# not a category to be rounded.


def test_a_stated_voltage_wins_over_a_near_miss_classification():
    stated = Requirements(input_source="24v-industrial", input_voltage=48.0)

    assert topology.power_source(stated).voltage == 48.0


def test_a_stated_voltage_is_cited_as_coming_from_the_brief():
    source = topology.power_source(Requirements(input_source="24v-industrial", input_voltage=48.0))

    assert source.is_inferred
    assert "brief" in source.basis


def test_a_stated_voltage_carries_no_invented_current_limit():
    """We know the volts because they were written down. Nothing stated the amps."""
    source = topology.power_source(Requirements(input_source="usb-5v", input_voltage=7.4))

    assert source.i_limit is None


def test_a_multicell_battery_keeps_its_classified_current_limit():
    """The cells identify their chemistry even though their series voltage is novel."""
    source = topology.power_source(Requirements(input_source="battery-3v7", input_voltage=7.4))

    assert source.voltage == 7.4
    assert source.i_limit == INPUT_SOURCES["battery-3v7"].i_limit
    assert source.basis == topology.INFERRED_BATTERY_LIMIT_BASIS


def test_a_stated_industrial_bus_does_not_inherit_a_current_limit():
    source = topology.power_source(Requirements(input_source="24v-industrial", input_voltage=48.0))

    assert source.i_limit is None


def test_an_unresolved_stated_supply_does_not_invent_a_current_limit():
    source = topology.power_source(Requirements(input_source="unresolved", input_voltage=7.4))

    assert source.i_limit is None


def test_a_stated_voltage_matching_a_known_supply_keeps_its_current_limit():
    """"12V barrel jack" states 12 V and the catalogue knows what one can deliver."""
    source = topology.power_source(Requirements(input_source="12v-barrel", input_voltage=12.0))

    assert source.voltage == 12.0
    assert source.i_limit == INPUT_SOURCES["12v-barrel"].i_limit


def test_no_stated_voltage_still_uses_the_classification():
    assert topology.power_source(Requirements(input_source="12v-barrel")).voltage == 12.0


def test_an_unknown_source_with_a_stated_voltage_does_not_raise():
    """A dynamo is in no vocabulary, but "12 V" is still a fact about the board."""
    source = topology.power_source(Requirements(input_source="unresolved", input_voltage=12.0))

    assert source.voltage == 12.0


def test_a_near_match_keeps_the_catalogue_current_limit():
    """A coin cell stated as 3.0 V must not lose its 20 mA ceiling.

    The current limit is what catches a 240 mA radio on a CR2032. Dropping it because
    the brief rounded the voltage differently would disable R4 without saying so, which
    is the same class of silent loss the stated voltage exists to prevent.
    """
    source = topology.power_source(Requirements(input_source="battery-3v0", input_voltage=3.05))

    assert source.i_limit == INPUT_SOURCES["battery-3v0"].i_limit


def test_a_material_battery_disagreement_keeps_its_classified_current_limit():
    source = topology.power_source(Requirements(input_source="battery-3v7", input_voltage=7.4))

    assert source.voltage == 7.4
    assert source.i_limit == INPUT_SOURCES["battery-3v7"].i_limit


def test_a_data_edge_id_survives_the_master_changing():
    """Plan time takes the master from the declared link; `data_edges` infers the one
    that actually offers the bus. On a two-master board those differ, and an id keyed on
    the pair meant the plan declared `bus-mcu-sensor` and the patch named
    `bus-mcu2-sensor` — landing on an id the client never saw, leaving the real edge
    `pending` for ever. Edge patches merge by id and have no delete."""
    planned = topology.planned_data_edges((("spi_mcu", "sensor"),))
    slots = {
        "i2c_mcu": slot("i2c_mcu", parts.esp32s3(interfaces=("I2C",), role="master")),
        "spi_mcu": slot("spi_mcu", parts.esp32s3(interfaces=("SPI",), role="master")),
        "sensor": slot("sensor", parts.sht31(interfaces=("I2C",), role="peripheral")),
    }

    resolved = [e for e in topology.data_edges(slots) if e.target == "sensor"]

    assert resolved[0].source == "i2c_mcu", "inference picks the master offering the bus"
    assert planned[0].source == "spi_mcu", "the plan kept the declared link"
    assert planned[0].id == resolved[0].id == "bus-sensor", "same id, so the patch lands"


def test_one_data_edge_per_peripheral_even_when_two_links_claim_it():
    edges = topology.planned_data_edges((("mcu_a", "sensor"), ("mcu_b", "sensor")))

    assert [e.id for e in edges] == ["bus-sensor"]
    assert edges[0].source == "mcu_a", "first link wins, as power_edges does for rails"
