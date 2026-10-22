"""Rule-by-rule coverage: every rule's pass, warn and fail path.

The assertions check the *verdict* — status, subject, involved, evidence — rather than
the exact wording of `detail`, except where the wording is the thing on stage.
"""

from __future__ import annotations

import pytest
from dataclasses import replace

from continuity.engine import draw as rail_current, packages, rules
from continuity.engine.models import Evidence, PartSpec, Requirements
from tests import parts
from tests.boards import slot, usb_board


def only(verdicts, rule: str, subject: str | None = None, scope: str | None = None):
    """The single verdict matching (rule, subject, scope) — the key a `check` event uses."""
    matches = [
        v
        for v in verdicts
        if v.rule == rule
        and (subject is None or v.subject == subject)
        and (scope is None or v.scope == scope)
    ]
    assert len(matches) == 1, f"expected one {rule} verdict, got {[v.detail for v in matches]}"
    return matches[0]


RAIL = "3V3"
USB = "5V0"


# ── R1 · voltage_overlap ──────────────────────────────────────────────────────


def test_part_within_rail_range_passes():
    board = usb_board(regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3()})

    verdict = only(rules.voltage_overlap(board), "voltage_overlap", "mcu")

    assert verdict.status == "pass"
    assert "3 V–3.6 V" in verdict.detail


def test_part_below_rail_range_fails_and_implicates_the_rail_source():
    board = usb_board(
        regulator=parts.ap2112k(vout_min=5.0, vout_max=5.0),
        loads={"mcu": parts.esp32s3()},
        rail_voltage=5.0,
    )

    verdict = only(rules.voltage_overlap(board), "voltage_overlap", "mcu")

    assert verdict.status == "fail"
    assert "is above that" in verdict.detail
    # the regulator is implicated too — changing its output is a legitimate fix
    assert set(verdict.involved) == {"mcu", "regulator"}


def test_a_one_sided_rating_still_catches_an_overvoltage():
    """Real payloads often state only a maximum. A part at twice its rating is a fail,
    not an "unchecked" — see `_check_one_supply`."""
    board = usb_board(
        regulator=parts.ap2112k(vout_min=5.0, vout_max=5.0),
        loads={"mcu": parts.esp32s3(vmin=None, vmax=3.6)},
        rail_voltage=5.0,
    )

    verdict = only(rules.voltage_overlap(board), "voltage_overlap", "mcu")

    assert verdict.status == "fail"
    assert "rated to 3.6 V" in verdict.detail


def test_a_satisfied_one_sided_rating_warns_about_the_missing_bound():
    board = usb_board(
        regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3(vmin=None, vmax=3.6)}
    )

    verdict = only(rules.voltage_overlap(board), "voltage_overlap", "mcu")

    assert verdict.status == "warn"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"
    assert "publishes no minimum" in verdict.detail


def test_a_missing_maximum_is_named_too():
    board = usb_board(
        regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3(vmin=3.0, vmax=None)}
    )

    assert "publishes no maximum" in only(
        rules.voltage_overlap(board), "voltage_overlap", "mcu"
    ).detail


def test_unstated_supply_range_warns_rather_than_passing_quietly():
    board = usb_board(
        regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3(vmin=None, vmax=None)}
    )

    verdict = only(rules.voltage_overlap(board), "voltage_overlap", "mcu")

    assert verdict.status == "warn"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"
    assert "could not be checked" in verdict.detail


def test_voltage_verdict_quotes_the_distributor_parameter_verbatim():
    board = usb_board(regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3()})

    verdict = only(rules.voltage_overlap(board), "voltage_overlap", "mcu")

    assert verdict.evidence[0].field == "Voltage - Supply"
    assert verdict.evidence[0].value == "3.0V ~ 3.6V"
    assert verdict.evidence[0].source == parts.DATASHEET


def test_derived_sequence_evidence_renders_as_a_readable_list():
    part = PartSpec(
        mpn="DERIVED-INTERFACES",
        manufacturer="Test manufacturer",
        description="Test part",
        category="test",
        interfaces=("I2C", "SPI", "UART", "USB", "CAN"),
    )

    evidence = part.cite("part", "interfaces")

    assert evidence[0].field == "interfaces (derived)"
    assert evidence[0].value == "I2C, SPI, UART, USB and CAN"
    assert "(" not in evidence[0].value
    assert "'" not in evidence[0].value


def test_derived_single_element_sequence_evidence_has_no_trailing_conjunction():
    part = PartSpec(
        mpn="DERIVED-ONE-INTERFACE",
        manufacturer="Test manufacturer",
        description="Test part",
        category="test",
        interfaces=("I2C",),
    )

    evidence = part.cite("part", "interfaces")

    assert evidence[0].value == "I2C"


def test_derived_empty_sequence_evidence_does_not_render_a_tuple_repr():
    part = PartSpec(
        mpn="DERIVED-NO-INTERFACES",
        manufacturer="Test manufacturer",
        description="Test part",
        category="test",
        interfaces=(),
    )

    evidence = part.cite("part", "interfaces")

    assert evidence[0].value == "nothing"


def test_payload_quoted_evidence_value_is_byte_for_byte_unchanged():
    payload_value = "I2C, SPI ('raw payload')"
    part = PartSpec(
        mpn="QUOTED-INTERFACES",
        manufacturer="Test manufacturer",
        description="Test part",
        category="test",
        interfaces=("I2C", "SPI"),
        raw={"Interfaces": payload_value},
        provenance={"interfaces": "Interfaces"},
    )

    evidence = part.cite("part", "interfaces")

    assert evidence[0].field == "Interfaces"
    assert evidence[0].value == payload_value


def test_none_evidence_value_is_omitted_instead_of_rendering_none():
    part = PartSpec(
        mpn="NO-INTERFACES",
        manufacturer="Test manufacturer",
        description="Test part",
        category="test",
        interfaces=None,  # type: ignore[arg-type]
    )

    assert part.cite("part", "interfaces") == ()


def test_shared_raw_parameter_is_cited_once_not_twice():
    """vmin and vmax both come from "Voltage - Supply"; quoting it twice looks padded."""
    board = usb_board(regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3()})

    verdict = only(rules.voltage_overlap(board), "voltage_overlap", "mcu")

    assert len(verdict.evidence) == 1


# ── R2 · interface_role_match ─────────────────────────────────────────────────


def test_shared_bus_passes():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(), "sensor": parts.sht31()},
    )

    verdict = only(rules.interface_role_match(board), "interface_role_match", "sensor")

    assert verdict.status == "pass"
    assert "on I2C" in verdict.detail


def test_an_smbus_sensor_is_satisfied_by_an_i2c_master():
    """TMP461AIRUNR-S triggered a false conflict: SMBus devices run on I²C masters."""
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={
            "mcu": parts.esp32s3(interfaces=("I2C",)),
            "sensor": parts.sht31(mpn="TMP461AIRUNR-S", interfaces=("SMBus",)),
        },
    )

    verdict = only(rules.interface_role_match(board), "interface_role_match", "sensor")

    assert verdict.status == "pass"
    assert "SMBus" in verdict.detail
    assert "I2C" in verdict.detail


def test_an_i2c_sensor_is_not_satisfied_by_an_smbus_only_master():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={
            "mcu": parts.esp32s3(interfaces=("SMBus",)),
            "sensor": parts.sht31(interfaces=("I2C",)),
        },
    )

    verdict = only(rules.interface_role_match(board), "interface_role_match", "sensor")

    assert verdict.status == "fail"


def test_single_bus_matches_one_wire():
    """T1601B reaches payloads as SINGLE BUS, a distributor rendering of 1-Wire."""
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={
            "mcu": parts.esp32s3(interfaces=("1-WIRE",)),
            "sensor": parts.sht31(mpn="T1601B", interfaces=("SINGLE BUS",)),
        },
    )

    verdict = only(rules.interface_role_match(board), "interface_role_match", "sensor")

    assert verdict.status == "pass"


def test_twi_and_i2c_masters_are_detected_as_bus_contention():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={
            "mcu": parts.esp32s3(interfaces=("TWI",)),
            "flash": parts.spi_flash(role="master", interfaces=("I2C",)),
        },
    )

    contention = [
        verdict
        for verdict in rules.interface_role_match(board)
        if verdict.status == "fail" and "only one may" in verdict.detail
    ]

    assert len(contention) == 1


def test_chip_select_pressure_canonicalises_spi_spelling():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={
            "mcu": parts.esp32s3(pins_available=5),
            "flash": parts.spi_flash(),
            "display": parts.oled(pins_required=1, interfaces=(" spi ",)),
        },
    )

    warnings = [
        verdict
        for verdict in rules.interface_role_match(board)
        if verdict.status == "warn" and "chip select" in verdict.detail
    ]

    assert len(warnings) == 1
    assert "2 SPI peripherals" in warnings[0].detail


def test_no_shared_bus_fails():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={
            "mcu": parts.esp32s3(interfaces=("SPI",)),
            "sensor": parts.sht31(interfaces=("I2C",)),
        },
    )

    verdict = only(rules.interface_role_match(board), "interface_role_match", "sensor")

    assert verdict.status == "fail"
    assert "no shared bus" in verdict.detail


def test_two_masters_on_one_bus_fails():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={
            "mcu": parts.esp32s3(),
            "flash": parts.spi_flash(role="master", interfaces=("SPI",)),
        },
    )

    contention = [
        v
        for v in rules.interface_role_match(board)
        if v.status == "fail" and "only one may" in v.detail
    ]

    assert len(contention) == 1
    assert set(contention[0].involved) == {"mcu", "flash"}


def test_peripheral_with_no_controller_fails():
    board = usb_board(
        regulator=parts.ap2112k(), loads={"sensor": parts.sht31()}, pinned=("sensor",)
    )

    verdict = only(rules.interface_role_match(board), "interface_role_match", "sensor")

    assert verdict.status == "fail"
    assert "no controller" in verdict.detail


def test_more_spi_peripherals_than_free_gpio_warns():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={
            "mcu": parts.esp32s3(pins_available=5),
            "flash": parts.spi_flash(),  # 4 pins, SPI
            "display": parts.oled(pins_required=1, interfaces=("SPI",)),
        },
    )

    warnings = [
        v for v in rules.interface_role_match(board) if v.status == "warn" and "chip select" in v.detail
    ]

    assert len(warnings) == 1


# ── R3 · pin_budget ───────────────────────────────────────────────────────────


def test_pin_budget_passes_within_the_gpio_count():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(), "sensor": parts.sht31(), "display": parts.oled()},
    )

    verdict = only(rules.pin_budget(board), "pin_budget")

    assert verdict.status == "pass"
    assert "4 of 36 GPIO" in verdict.detail


def test_pin_budget_fails_and_says_how_short():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(pins_available=3), "sensor": parts.sht31(), "display": parts.oled()},
    )

    verdict = only(rules.pin_budget(board), "pin_budget")

    assert verdict.status == "fail"
    assert "1 short" in verdict.detail


def test_pin_budget_warns_when_the_controller_states_no_gpio_count():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(pins_available=None), "sensor": parts.sht31()},
    )

    assert only(rules.pin_budget(board), "pin_budget").status == "warn"
    assert only(rules.pin_budget(board), "pin_budget").unchecked


# ── R4 · current_budget ───────────────────────────────────────────────────────


def test_current_budget_passes_with_headroom():
    board = usb_board(regulator=parts.ap2112k(), loads={"sensor": parts.sht31()})

    verdict = only(rules.current_budget(board), "current_budget", "regulator", RAIL)

    assert verdict.status == "pass"
    assert verdict.detail == "1.5 mA of 600 mA (<1%)"


def test_current_budget_warns_inside_the_derating_band():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(), "sensor": parts.sht31()},
    )

    verdict = only(rules.current_budget(board), "current_budget", "regulator", RAIL)

    assert verdict.status == "warn"
    assert not verdict.unchecked, "every figure was known; this is a genuine warning"
    assert "derating band" in verdict.detail


def test_current_budget_fails_once_margin_pushes_it_over():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(), "sensor": parts.sht31(), "display": parts.oled()},
    )

    verdict = only(rules.current_budget(board), "current_budget", "regulator", RAIL)

    assert verdict.status == "fail"
    assert "above the 600 mA rating" in verdict.detail
    assert set(verdict.involved) == {"regulator", "mcu", "sensor", "display"}


def test_current_margin_is_what_moves_a_board_from_pass_to_fail():
    """The margin is a requirement, not a constant — a battery brief tightens it."""
    loads = {"mcu": parts.esp32s3(i_peak=0.500), "sensor": parts.sht31()}
    relaxed = usb_board(
        regulator=parts.ap2112k(i_max=0.700),
        loads=loads,
        requirements=Requirements(current_margin=0.15),
    )
    battery = usb_board(
        regulator=parts.ap2112k(i_max=0.700),
        loads=loads,
        requirements=Requirements(current_margin=0.50),
    )

    assert only(rules.current_budget(relaxed), "current_budget", "regulator", RAIL).status != "fail"
    assert only(rules.current_budget(battery), "current_budget", "regulator", RAIL).status == "fail"


def test_regulator_input_current_reflects_its_output_rail():
    """An LDO passes output current straight through; the 5 V budget must see it."""
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(), "sensor": parts.sht31()},
    )

    verdicts = rules.current_budget(board)
    regulated = only(verdicts, "current_budget", "regulator", RAIL)
    upstream = only(verdicts, "current_budget", "regulator", USB)

    # Same subject, different net — which is exactly why verdicts carry a scope.
    assert regulated.status == "warn"
    assert not regulated.unchecked, "every figure was known; this is a genuine warning"
    assert regulated.detail.startswith("501.5 mA of 600 mA")
    assert upstream.status == "pass"
    assert upstream.detail == "501.5 mA of 3000 mA (17%)"


def test_switcher_draws_less_input_current_than_it_delivers():
    linear = usb_board(regulator=parts.ap2112k(i_max=1.0), loads={"mcu": parts.esp32s3()})
    switching = usb_board(regulator=parts.buck_3v3(), loads={"mcu": parts.esp32s3()})

    def usb_draw(board):
        rail = board.rails["5V0"]
        draw, _ = rail_current.rail_draw(board, rail_current.consumers(board, rail))
        return draw

    assert usb_draw(linear) == pytest.approx(0.500, abs=1e-6)
    assert usb_draw(switching) == pytest.approx(3.3 * 0.500 / (0.92 * 5.0), abs=1e-6)


def test_unstated_draw_reports_a_lower_bound_rather_than_a_pass():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(), "sensor": parts.sht31(i_typ=None, i_peak=None)},
    )

    verdict = only(rules.current_budget(board), "current_budget", "regulator", RAIL)

    assert verdict.status == "warn"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"
    assert verdict.detail.startswith("At least")
    assert "the real figure is higher" in verdict.detail


def test_a_provable_overload_fails_even_with_a_part_that_states_no_draw():
    """The unknown parts can only add current. Waiting for complete data before
    deciding would report "unchecked" on a board already provably over budget."""
    board = usb_board(
        regulator=parts.ap2112k(i_max=0.4),
        loads={"mcu": parts.esp32s3(), "sensor": parts.sht31(i_typ=None, i_peak=None)},
    )

    verdict = only(rules.current_budget(board), "current_budget", "regulator", RAIL)

    assert verdict.status == "fail"
    assert "that is a floor" in verdict.detail


def test_thermal_computes_from_a_partial_draw_rather_than_declining():
    board = usb_board(
        regulator=parts.ap2112k(i_max=1.0),
        loads={"mcu": parts.esp32s3(i_peak=0.7), "sensor": parts.sht31(i_typ=None, i_peak=None)},
    )

    verdict = only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator", RAIL)

    assert verdict.status == "fail"
    assert "at least" in verdict.detail
    assert "This is a floor" in verdict.detail


def test_a_thermally_fine_board_with_unknown_draws_warns_rather_than_passes():
    board = usb_board(
        regulator=parts.buck_3v3(),
        loads={"mcu": parts.esp32s3(), "sensor": parts.sht31(i_typ=None, i_peak=None)},
    )

    verdict = only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator", RAIL)

    assert verdict.status == "warn"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"


def test_an_unstated_pin_count_is_not_counted_as_zero():
    """Otherwise a full board reports room to spare.

    Tightened 9 Aug. This used to assert `warn — At least 2 of 4`, because a peripheral
    with no stated pin count was unknowable and the verdict could only call the total a
    floor. It is no longer unknowable: the OLED states I²C, and an I²C device costs two
    pins whether or not the distributor says so. The board now reads exactly full
    instead of half empty, which is the outcome this test was always reaching for.
    """
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={
            "mcu": parts.esp32s3(pins_available=4),
            "sensor": parts.sht31(pins_required=2),
            "display": parts.oled(pins_required=None),
        },
    )

    verdict = only(rules.pin_budget(board), "pin_budget")

    assert verdict.detail.startswith("4 of 4 GPIO"), "the display must not count as zero"


def test_no_current_rating_warns_rather_than_assuming_one():
    board = usb_board(regulator=parts.ap2112k(i_max=None), loads={"mcu": parts.esp32s3()})

    verdict = only(rules.current_budget(board), "current_budget", "regulator", RAIL)

    assert verdict.status == "warn"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"
    assert "does not state a current rating" in verdict.detail


# ── R5 · thermal_dissipation ──────────────────────────────────────────────────


def test_linear_regulator_dissipation_is_input_minus_output_times_draw():
    board = usb_board(
        regulator=parts.ap2112k(i_max=1.0),
        loads={"mcu": parts.esp32s3(i_peak=0.700)},
    )

    verdict = only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator", RAIL)

    assert verdict.status == "fail"
    assert "(5 V − 3.3 V) × 700 mA = 1.19 W" in verdict.detail


def test_switching_regulator_barely_warms():
    board = usb_board(regulator=parts.buck_3v3(), loads={"mcu": parts.esp32s3(i_peak=0.700)})

    verdict = only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator", RAIL)

    assert verdict.status == "pass"
    assert "92% efficient" in verdict.detail


def _bounded_buck_board(draw: float):
    return usb_board(
        regulator=parts.buck_3v3(
            efficiency=None,
            synchronous=True,
            package="SOT-23-5",
            temp_max=125,
        ),
        loads={"mcu": parts.esp32s3(i_peak=draw)},
    )


def test_a_switcher_that_passes_at_the_worst_efficiency_is_a_confident_pass():
    verdict = only(
        rules.thermal_dissipation(_bounded_buck_board(0.4)),
        "thermal_dissipation",
        "regulator",
        RAIL,
    )

    assert verdict.status == "pass"
    assert "worst case" in verdict.detail
    assert "85%" in verdict.detail


def test_a_switcher_that_fails_at_the_best_efficiency_is_a_confident_fail():
    verdict = only(
        rules.thermal_dissipation(_bounded_buck_board(3.0)),
        "thermal_dissipation",
        "regulator",
        RAIL,
    )

    assert verdict.status == "fail"
    assert "best case" in verdict.detail
    assert "95%" in verdict.detail


def test_a_switcher_straddling_its_temperature_limit_names_the_break_even_efficiency():
    draw = 0.8
    verdict = only(
        rules.thermal_dissipation(_bounded_buck_board(draw)),
        "thermal_dissipation",
        "regulator",
        RAIL,
    )
    p_crit = (125 - 25) / 250
    eta_crit = 1 / (p_crit / (3.3 * draw) + 1)

    assert verdict.status == "warn"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"
    assert f"~{round(eta_crit * 100)}%" in verdict.detail
    assert "efficiency curve at 3.3 V and 800 mA" in verdict.detail


def test_a_linear_regulator_keeps_its_existing_thermal_wording_and_evidence():
    board = usb_board(
        regulator=parts.ap2112k(i_max=1.0),
        loads={"mcu": parts.esp32s3(i_peak=0.7)},
    )

    verdict = only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator", RAIL)

    assert verdict.status == "fail"
    assert verdict.detail == (
        "(5 V − 3.3 V) × 700 mA = 1.19 W in SOT-23-5 — "
        "298 °C rise, 322 °C junction against a 125 °C limit."
    )
    assert [e.field for e in verdict.evidence] == [
        "Package / Case",
        "Operating Temperature",
        "θJA (package table)",
        "current basis",
    ]


def test_a_switching_regulator_is_identified_from_its_category_without_topology():
    """TPS23754PWPR is a 48 V PoE flyback controller; treating it as linear overstated
    its heat from about 1 W to 8.6 W and raised a false thermal conflict."""
    regulator = parts.buck_3v3(
        mpn="TPS23754PWPR",
        topology=None,
        category=" Power Over Ethernet (PoE) Controllers ",
    )
    board = usb_board(regulator=regulator, loads={"mcu": parts.esp32s3(i_peak=0.700)})

    assert regulator.regulation == "switching"
    assert only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator", RAIL).status == "pass"


def test_a_linear_regulator_is_identified_from_its_category_without_topology():
    regulator = parts.ap2112k(
        i_max=1.0,
        topology=None,
        category="Voltage Regulators - Linear, Low Drop Out (LDO) Regulators",
    )
    board = usb_board(regulator=regulator, loads={"mcu": parts.esp32s3(i_peak=0.700)})

    assert regulator.regulation == "linear"
    verdict = only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator", RAIL)
    assert verdict.status == "fail"
    assert "(5 V − 3.3 V) × 700 mA = 1.19 W" in verdict.detail


def test_an_unclassified_regulator_warns_instead_of_assuming_linear_dissipation():
    """An unstated topology is not proof of a linear regulator, so a computed thermal
    pass or fail would be a guess rather than an engine verdict."""
    regulator = parts.ap2112k(i_max=1.0, topology=None, category="Unclassified Regulator")
    board = usb_board(regulator=regulator, loads={"mcu": parts.esp32s3(i_peak=0.700)})

    assert regulator.regulation is None
    verdict = only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator", RAIL)
    assert verdict.status == "warn"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"
    assert "no topology" in verdict.detail


def test_thermal_cites_theta_ja_against_the_package_table_not_the_datasheet():
    """A verdict must never imply the datasheet said something it did not."""
    board = usb_board(
        regulator=parts.ap2112k(i_max=1.0), loads={"mcu": parts.esp32s3(i_peak=0.700)}
    )

    verdict = only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator", RAIL)
    theta = [e for e in verdict.evidence if "θJA" in e.field]

    assert theta == [
        Evidence(
            "regulator",
            "θJA (package table)",
            "250 °C/W",
            packages.THETA_JA_SOURCE,
        )
    ]


def test_thermal_uses_a_datasheet_theta_ja_and_quotes_its_source_line():
    line = "RθJA Junction-to-ambient thermal resistance 116.3 48.7"
    board = usb_board(
        regulator=parts.ap2112k(theta_ja=116.3, theta_ja_source_line=line),
        loads={"mcu": parts.esp32s3(i_peak=0.700)},
    )

    verdict = only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator", RAIL)
    theta = [e for e in verdict.evidence if "θJA" in e.field]

    assert theta == [
        Evidence(
            "regulator",
            "θJA (datasheet)",
            '116 °C/W — "RθJA Junction-to-ambient thermal resistance 116.3 48.7"',
            parts.DATASHEET,
        )
    ]


def test_unknown_package_warns_instead_of_guessing_a_theta_ja():
    board = usb_board(
        regulator=parts.ap2112k(i_max=1.0, package="XYZ-99", theta_ja=None),
        loads={"mcu": parts.esp32s3(i_peak=0.700)},
    )

    verdict = only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator", RAIL)

    assert verdict.status == "warn"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"
    assert "no θJA is known" in verdict.detail


def test_hot_but_legal_part_warns():
    board = usb_board(
        regulator=parts.ap2112k(i_max=1.0, package="SOT-223"),
        loads={"mcu": parts.esp32s3(i_peak=0.700)},
        requirements=Requirements(temp_range=(-40, 125)),
    )

    verdict = only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator", RAIL)

    assert verdict.status == "warn"
    assert not verdict.unchecked, "every figure was known; this is a genuine warning"
    assert "runs hot" in verdict.detail


def test_switcher_with_no_efficiency_figure_falls_back_to_the_assumption():
    """Unstated switcher efficiency is bounded by its published categorical facts."""
    board = usb_board(
        regulator=parts.buck_3v3(efficiency=None), loads={"mcu": parts.esp32s3()}
    )

    verdict = only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator", RAIL)

    assert "states no efficiency figure" not in verdict.detail
    bounded = next(e for e in verdict.evidence if e.field == "efficiency (bounded)")
    assert bounded.value == "75%–95%"
    assert "rectifier type not stated" in (bounded.source or "")


# ── R6 · availability ─────────────────────────────────────────────────────────


def test_out_of_stock_part_fails():
    board = usb_board(regulator=parts.ap2112k(), loads={"sensor": parts.sht40()})

    verdict = only(rules.availability(board), "availability", "sensor")

    assert verdict.status == "fail"
    assert verdict.detail == "SHT40-AD1B-R2: 0 in stock at JLCPCB, below the 100 minimum."
    assert verdict.involved == ("sensor",)


def test_stocked_part_passes():
    board = usb_board(regulator=parts.ap2112k(), loads={"sensor": parts.sht31()})

    assert only(rules.availability(board), "availability", "sensor").status == "pass"


def test_end_of_life_part_warns_even_when_stocked():
    board = usb_board(
        regulator=parts.ap2112k(), loads={"sensor": parts.sht31(lifecycle="nrnd")}
    )

    verdict = only(rules.availability(board), "availability", "sensor")

    assert verdict.status == "warn"
    assert not verdict.unchecked, "every figure was known; this is a genuine warning"
    assert "not recommended for new designs" in verdict.detail


def test_long_lead_time_warns():
    board = usb_board(
        regulator=parts.ap2112k(), loads={"sensor": parts.sht31(lead_time_days=56)}
    )

    verdict = only(rules.availability(board), "availability", "sensor")

    assert verdict.status == "warn"
    assert not verdict.unchecked, "every figure was known; this is a genuine warning"
    assert "56-day lead time" in verdict.detail


def test_stock_floor_comes_from_the_brief():
    prototype = usb_board(
        regulator=parts.ap2112k(),
        loads={"sensor": parts.sht31(stock=50)},
        requirements=Requirements(min_stock=10),
    )
    production = usb_board(
        regulator=parts.ap2112k(),
        loads={"sensor": parts.sht31(stock=50)},
        requirements=Requirements(min_stock=1000),
    )

    assert only(rules.availability(prototype), "availability", "sensor").status == "pass"
    assert only(rules.availability(production), "availability", "sensor").status == "fail"


def test_missing_stock_figure_warns():
    board = usb_board(regulator=parts.ap2112k(), loads={"sensor": parts.sht31(stock=None)})

    assert only(rules.availability(board), "availability", "sensor").status == "warn"
    assert only(rules.availability(board), "availability", "sensor").unchecked


# ── R7 · temperature_rating ──────────────────────────────────────────────────


def test_commercial_part_fails_the_cold_end_of_an_industrial_board():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={
            "sensor": parts.sht31(
                temp_min=0,
                temp_max=70,
                raw={"Temperature Minimum": "0°C", "Temperature Maximum": "70°C"},
                provenance={
                    "temp_min": "Temperature Minimum",
                    "temp_max": "Temperature Maximum",
                },
            )
        },
        requirements=Requirements(temp_range=(-40, 85)),
    )

    verdict = only(rules.temperature_rating(board), "temperature_rating", "sensor")

    assert verdict.status == "fail"
    assert "cold" in verdict.detail
    assert "40" in verdict.detail
    assert [e.field for e in verdict.evidence] == ["Temperature Minimum", "Temperature Maximum"]


def test_part_below_the_required_hot_end_fails():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={
            "sensor": parts.sht31(
                temp_min=-40,
                temp_max=70,
                raw={"Temperature Maximum": "70°C"},
                provenance={"temp_max": "Temperature Maximum"},
            )
        },
        requirements=Requirements(temp_range=(-40, 85)),
    )

    verdict = only(rules.temperature_rating(board), "temperature_rating", "sensor")

    assert verdict.status == "fail"
    assert "hot" in verdict.detail
    assert "15" in verdict.detail
    assert [e.field for e in verdict.evidence] == ["Temperature Maximum"]


def test_industrial_rated_part_passes_on_an_industrial_board():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"sensor": parts.sht31(temp_min=-40, temp_max=85)},
        requirements=Requirements(temp_range=(-40, 85)),
    )

    assert only(rules.temperature_rating(board), "temperature_rating", "sensor").status == "pass"


def test_commercial_rated_part_passes_on_a_commercial_board():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"sensor": parts.sht31(temp_min=0, temp_max=70)},
        requirements=Requirements(temp_range=(0, 70)),
    )

    assert only(rules.temperature_rating(board), "temperature_rating", "sensor").status == "pass"


def test_part_with_no_temperature_bounds_warns_naming_the_missing_fields():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"sensor": parts.sht31(temp_min=None, temp_max=None)},
    )

    verdict = only(rules.temperature_rating(board), "temperature_rating", "sensor")

    assert verdict.status == "warn"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"
    assert "temp_min" in verdict.detail
    assert "temp_max" in verdict.detail


def test_one_sided_temperature_rating_still_catches_a_hot_end_failure():
    """A known inadequate bound must fail even if the other bound is unstated."""
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"sensor": parts.sht31(temp_min=None, temp_max=70)},
        requirements=Requirements(temp_range=(-40, 85)),
    )

    verdict = only(rules.temperature_rating(board), "temperature_rating", "sensor")

    assert verdict.status == "fail"
    assert "hot" in verdict.detail


def test_satisfied_one_sided_temperature_rating_warns_about_the_missing_bound():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"sensor": parts.sht31(temp_min=None, temp_max=85)},
        requirements=Requirements(temp_range=(-40, 85)),
    )

    verdict = only(rules.temperature_rating(board), "temperature_rating", "sensor")

    assert verdict.status == "warn"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"
    assert "temp_min" in verdict.detail


def test_slot_without_a_part_has_no_temperature_verdict():
    board = usb_board(regulator=parts.ap2112k(), loads={"sensor": parts.sht31()})
    mid_placement = replace(board, slots={**board.slots, "future": slot("future")})

    assert [v.subject for v in rules.temperature_rating(mid_placement)] == ["regulator", "sensor"]


# ── footprint · warning only ──────────────────────────────────────────────────


def test_footprint_is_silent_unless_a_size_target_was_asked_for():
    board = usb_board(regulator=parts.ap2112k(package="TO-263"), loads={"mcu": parts.esp32s3()})

    assert rules.footprint(board) == []


def test_footprint_warns_over_the_size_target_but_never_fails():
    board = usb_board(
        regulator=parts.ap2112k(package="TO-263"),
        loads={"mcu": parts.esp32s3()},
        requirements=Requirements(max_package_mm=4.0),
    )

    verdicts = rules.footprint(board)

    assert [v.status for v in verdicts] == ["warn"]
    assert "10 mm on its longest side" in verdicts[0].detail


# ── the suite ─────────────────────────────────────────────────────────────────


# ── R9 · rail_coverage ────────────────────────────────────────────────────────


def test_a_part_on_no_rail_is_reported_as_unchecked():
    """The solar board's charge controller: on the BOM, and on no net anybody checked."""
    board = usb_board(regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3()})
    board = replace(
        board,
        slots={**board.slots, "charger": slot("charger", parts.ap2112k(), tier="power")},
    )

    verdict = only(rules.evaluate(board), "rail_coverage", subject="charger")

    assert verdict.status == "warn", "a gap in what we know, not a fault in the board"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"
    assert "no modelled power rail" in verdict.detail
    # It has to name the checks it missed, or the reader cannot tell this part apart from
    # one that passed everything. Only the three rail-based rules are named: the others ran,
    # and saying they "were checked" would overclaim when they simply had nothing to say.
    assert "thermal dissipation" in verdict.detail
    assert "could not be checked" in verdict.detail


def test_a_board_whose_parts_are_all_on_rails_reports_no_coverage_gap():
    board = usb_board(regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3()})

    assert [v for v in rules.evaluate(board) if v.rule == "rail_coverage"] == []


def test_an_unfilled_slot_on_no_rail_says_nothing_yet():
    """Nothing to report until a part is chosen — that is `availability`'s job, not this."""
    board = usb_board(regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3()})
    board = replace(board, slots={**board.slots, "charger": slot("charger", None)})

    assert [v for v in rules.evaluate(board) if v.rule == "rail_coverage"] == []


def test_coverage_never_fails_so_it_cannot_start_a_repair_loop():
    """There is nothing a repair could change: no rule has found fault with the part."""
    board = usb_board(regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3()})
    board = replace(
        board,
        slots={**board.slots, "charger": slot("charger", parts.ap2112k(), tier="power")},
    )

    coverage = [v for v in rules.evaluate(board) if v.rule == "rail_coverage"]

    assert coverage and all(v.status == "warn" for v in coverage)
    assert all(v.unchecked for v in coverage)
    # The board has its own faults; none of them is this rule's, and the slot it reports
    # on contributes nothing to the failure set that drives a repair.
    assert "charger" not in {v.subject for v in rules.failures(rules.evaluate(board))}


def test_every_verdict_names_a_rule_the_contract_declares():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(), "sensor": parts.sht40(), "display": parts.oled()},
        requirements=Requirements(max_package_mm=4.0),
    )

    from continuity.engine.models import RULE_NAMES

    assert {v.rule for v in rules.evaluate(board)} <= set(RULE_NAMES)


def test_rule_names_and_functions_agree():
    from continuity.engine.models import RULE_NAMES
    from continuity.engine.integrity import PRECHECK_NAMES

    assert tuple(rule.__name__ for rule in rules.RULES) + PRECHECK_NAMES == RULE_NAMES


def test_subject_is_always_among_the_involved_slots():
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(), "sensor": parts.sht40(), "display": parts.oled()},
    )

    for verdict in rules.evaluate(board):
        assert verdict.subject in verdict.involved, verdict.detail


def test_evaluation_is_deterministic():
    """Same board in, same verdicts out — the demo cannot change under rehearsal."""
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(), "sensor": parts.sht40(), "display": parts.oled()},
    )

    first = [(v.rule, v.subject, v.status, v.detail) for v in rules.evaluate(board)]
    second = [(v.rule, v.subject, v.status, v.detail) for v in rules.evaluate(board)]

    assert first == second


def test_a_slot_is_never_listed_twice_as_involved():
    """The board input rail feeding a single regulator has that slot as both subject and
    only consumer — the drawer rendered it as two affected components."""
    from continuity.engine.models import Board, Rail

    board = Board(
        Requirements(),
        {"regulator": __import__("tests.boards", fromlist=["slot"]).slot("regulator", parts.ap2112k())},
        {"VIN": Rail("VIN", 5.0, None, ("regulator",), i_limit=1.5)},
    )

    verdict = only(rules.current_budget(board), "current_budget", "regulator", "VIN")

    assert verdict.involved == ("regulator",)


def test_a_peripheral_placed_before_its_controller_is_not_a_fault():
    """R2 is the one rule whose verdict depends on evaluation order. A motor driver
    placed ahead of the MCU sent whole live runs into repair loops over nothing."""
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(), "sensor": parts.sht31()},
    )
    mid_placement = replace(
        board, slots={**board.slots, "mcu": replace(board.slots["mcu"], part=None)}
    )

    verdict = only(rules.interface_role_match(mid_placement), "interface_role_match", "sensor")

    assert verdict.status == "warn"
    assert not verdict.unchecked, "an incomplete board is pending, not missing data"
    assert "no controller has been chosen yet" in verdict.detail


def test_a_complete_board_with_no_controller_is_a_fault():
    board = usb_board(
        regulator=parts.ap2112k(), loads={"sensor": parts.sht31()}, pinned=("sensor",)
    )

    verdict = only(rules.interface_role_match(board), "interface_role_match", "sensor")

    assert verdict.status == "fail"
    assert "no controller to drive it" in verdict.detail


# ── a regulator's working output is the rail it makes ─────────────────────────


def test_dissipation_reads_the_rail_not_an_adjustable_parts_range():
    """TPS5430DDAR is adjustable to 32 V. On a 3V3 rail it makes 3.3 V, not 32.

    Taking the range maximum gave `(5.0 − 32.04) × draw` — negative watts, which passes
    every thermal ceiling — and told the reviewer the board "required" 32.04 V.
    """
    adjustable = parts.ap2112k(vout_min=1.221, vout_max=32.0, topology="ldo", efficiency=None)
    board = usb_board(regulator=adjustable, loads={"mcu": parts.esp32s3()}, rail_voltage=3.3)

    verdict = only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator")

    assert verdict.status != "pass" or "−" not in verdict.detail
    assert "32" not in verdict.detail, "the adjustment range maximum must not reach a verdict"


def test_a_regulator_that_cannot_reach_the_rail_voltage_fails():
    """R1 checked every part as a *load* and never as a *source*.

    AMS1117-3.3 sourcing a 3V3 rail fed from 3.0 V is unbuildable — an LDO cannot boost —
    and two live battery boards shipped it with `0 conflict`.
    """
    ldo = parts.ap2112k(vout_min=3.3, vout_max=3.3)
    board = usb_board(regulator=ldo, loads={"mcu": parts.esp32s3()}, rail_voltage=1.8)

    verdict = only(rules.voltage_overlap(board), "voltage_overlap", "regulator", scope="3V3")

    assert verdict.status == "fail"
    assert "1.8" in verdict.detail


def test_a_linear_regulator_cannot_boost():
    board = usb_board(
        regulator=parts.ap2112k(vout_min=3.3, vout_max=3.3, topology="ldo"),
        loads={"mcu": parts.esp32s3()},
        rail_voltage=3.3,
        input_voltage=3.0,
    )

    verdict = only(rules.voltage_overlap(board), "voltage_overlap", "regulator", scope="3V3")

    assert verdict.status == "fail"
    assert "step up" in verdict.detail or "boost" in verdict.detail


def test_an_adjustable_regulator_set_within_its_range_passes():
    """The fix must not reject good parts — an adjustable covering the rail is fine."""
    board = usb_board(
        regulator=parts.ap2112k(vout_min=1.221, vout_max=32.0, topology="buck"),
        loads={"mcu": parts.esp32s3()},
        rail_voltage=3.3,
    )

    verdict = only(rules.voltage_overlap(board), "voltage_overlap", "regulator", scope="3V3")

    assert verdict.status == "pass"


def test_a_regulator_stating_no_output_range_is_unchecked_not_failed():
    board = usb_board(
        regulator=parts.ap2112k(vout_min=None, vout_max=None),
        loads={"mcu": parts.esp32s3()},
        rail_voltage=3.3,
    )

    verdict = only(rules.voltage_overlap(board), "voltage_overlap", "regulator", scope="3V3")

    assert verdict.status == "warn"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"


# ── switching regulators, with no efficiency published anywhere ────────────────
#
# `efficiency` was populated on 0 of 33 parts across six live boards. JLCPCB does not
# publish it, so R5's switching branch returned "states no efficiency figure" for every
# switcher — and `change_topology: buck` is the standard repair, so the engine's usual
# fix moved parts into the one place it could not check them.


def test_a_switcher_with_no_stated_efficiency_is_still_evaluated():
    board = usb_board(
        regulator=parts.ap2112k(topology="buck", efficiency=None, vout_min=3.3, vout_max=3.3),
        loads={"mcu": parts.esp32s3()},
    )

    verdict = only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator")

    assert "states no efficiency figure" not in verdict.detail
    assert verdict.status in {"pass", "warn", "fail"}


def test_an_efficiency_band_is_named_in_evidence():
    board = usb_board(
        regulator=parts.ap2112k(topology="buck", efficiency=None, vout_min=3.3, vout_max=3.3),
        loads={"mcu": parts.esp32s3()},
    )

    verdict = only(rules.thermal_dissipation(board), "thermal_dissipation", "regulator")
    bounded = next(e for e in verdict.evidence if e.field == "efficiency (bounded)")

    assert bounded.value == "75%–95%"
    assert "Continuity efficiency band" in (bounded.source or "")


def test_a_stated_efficiency_beats_the_band():
    stated = usb_board(
        regulator=parts.buck_3v3(synchronous=True),
        loads={"mcu": parts.esp32s3()},
    )
    bounded = usb_board(
        regulator=parts.ap2112k(topology="buck", efficiency=None, vout_min=3.3, vout_max=3.3),
        loads={"mcu": parts.esp32s3()},
    )

    a = only(rules.thermal_dissipation(stated), "thermal_dissipation", "regulator")
    b = only(rules.thermal_dissipation(bounded), "thermal_dissipation", "regulator")

    assert a.detail != b.detail, "a published figure must not be replaced by a band"
    assert any(e.field == "Efficiency" and e.source == parts.DATASHEET for e in a.evidence)
    assert not any(e.field == "efficiency (bounded)" for e in a.evidence)


def test_the_assumption_is_conservative():
    """R4 retains its conservative reflected-current input assumption."""
    assert 0 < rules.ASSUMED_EFFICIENCY <= 0.85


def test_r4_keeps_its_conservative_input_current_for_an_unstated_switcher_efficiency():
    board = usb_board(
        regulator=parts.buck_3v3(efficiency=None), loads={"mcu": parts.esp32s3()}
    )
    input_rail = board.rails[USB]
    draw, unstated = rail_current.rail_draw(board, rail_current.consumers(board, input_rail))

    assert unstated == []
    assert draw == pytest.approx(3.3 * 0.5 / (0.80 * 5.0))


# ── R3, made able to fail ─────────────────────────────────────────────────────
#
# `pins_required` was populated on 4 of 33 live parts and all four were *masters*, so
# the sum over peripherals was always 0 and `required > pins_available` was unreachable.
# R3 printed "3/3 checks passed" having measured nothing. Pin demand is now derived from
# bus topology, which needs no data a distributor withholds.


def small_mcu(gpio: int):
    return parts.esp32s3(pins_available=gpio, interfaces=("I2C", "SPI", "UART"), role="master")


def bus_device(bus: str, mpn: str):
    return parts.sht40(mpn=mpn, interfaces=(bus,), role="peripheral", pins_required=None)


def test_i2c_devices_share_one_pair_of_pins():
    """Three sensors on I²C is still two pins. Charging each device would be wrong."""
    board = usb_board(
        regulator=parts.buck_3v3(),
        loads={
            "mcu": small_mcu(4),
            "a": bus_device("I2C", "SENSOR-A"),
            "b": bus_device("I2C", "SENSOR-B"),
            "c": bus_device("I2C", "SENSOR-C"),
        },
        pinned=(),
    )

    assert only(rules.pin_budget(board), "pin_budget", "mcu").status == "pass"


def test_single_bus_uses_the_one_wire_pin_cost():
    board = usb_board(
        regulator=parts.buck_3v3(),
        loads={
            "mcu": parts.esp32s3(
                pins_available=1, interfaces=("1-WIRE",), role="master"
            ),
            "sensor": bus_device(" SINGLE BUS ", "T1601B"),
        },
        pinned=(),
    )

    verdict = only(rules.pin_budget(board), "pin_budget", "mcu")

    assert verdict.status == "pass"
    assert verdict.detail.startswith("1 of 1 GPIO")


def test_each_spi_device_needs_its_own_chip_select():
    """SPI shares clock and data but every device needs a select line."""
    board = usb_board(
        regulator=parts.buck_3v3(),
        loads={
            "mcu": small_mcu(5),
            "a": bus_device("SPI", "FLASH-A"),
            "b": bus_device("SPI", "FLASH-B"),
            "c": bus_device("SPI", "FLASH-C"),
        },
        pinned=(),
    )

    verdict = only(rules.pin_budget(board), "pin_budget", "mcu")

    assert verdict.status == "fail", "3 shared + 3 chip selects = 6 on a 5-GPIO part"
    assert "short" in verdict.detail


def test_a_stated_pin_count_beats_the_bus_estimate():
    board = usb_board(
        regulator=parts.buck_3v3(),
        loads={
            "mcu": small_mcu(4),
            "display": parts.oled(interfaces=("I2C",), pins_required=9, role="peripheral"),
        },
        pinned=(),
    )

    assert only(rules.pin_budget(board), "pin_budget", "mcu").status == "fail"


def test_a_peripheral_with_neither_a_bus_nor_a_count_is_still_named():
    board = usb_board(
        regulator=parts.buck_3v3(),
        loads={
            "mcu": small_mcu(8),
            "thing": parts.sht40(mpn="MYSTERY", interfaces=(), pins_required=None, role="peripheral"),
        },
        pinned=(),
    )

    verdict = only(rules.pin_budget(board), "pin_budget", "mcu")

    assert verdict.status == "warn"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"
    assert "MYSTERY" in verdict.detail or "state no pin count" in verdict.detail


def test_an_unknown_downstream_draw_does_not_pass_the_rail_above_it():
    """The coin-cell beacon: the one number the brief was about, reported as a clean pass.

    `reflected_draw` correctly returns None when what a regulator feeds is unknown — but
    `consumer_draw` treated that identically to "this part sources no rail" and fell back
    to the regulator's own datasheet current. A few hundred microamps of quiescent draw
    then stood in for a whole board, and a 20 mA cell reported `0 mA of 20 mA (0%)`.
    """
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(i_typ=None, i_peak=None, i_max=None)},
    )

    budgets = {
        v.scope: v for v in rules.current_budget(board) if v.rule == "current_budget"
    }

    assert budgets["3V3"].status == "warn", "the load states no draw"
    assert budgets[USB].status == "warn", "so neither does the rail feeding its regulator"
    assert budgets["3V3"].unchecked and budgets[USB].unchecked
    assert "states no draw" in budgets[USB].detail


def test_a_fully_stated_board_still_reflects_a_real_number_upstream():
    """The guard must not turn every reflected draw into an unknown."""
    board = usb_board(regulator=parts.ap2112k(), loads={"mcu": parts.esp32s3()})

    budget = only(rules.current_budget(board), "current_budget", scope=USB)

    assert budget.status == "pass"
    assert budget.detail.startswith("500 mA of"), "a real reflected figure, not a fallback"


# ── R2 · a part that speaks a bus but sits in neither role ────────────────────


def test_a_bus_speaking_part_with_no_role_is_reported_rather_than_skipped():
    """The CAN gateway that passed 4/4 over an MCU with no CAN controller.

    `_by_role` collects `master` and `peripheral` only, so a transceiver left `None` was
    absent from the rule entirely — not failed, not warned, not checked. A model failing
    to fill one field switched off an engine rule.
    """
    transceiver = parts.sht40(mpn="UJA1075ATW", interfaces=("LIN", "CAN"), role=None)
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(), "sensor": transceiver},
    )

    verdict = only(rules.interface_role_match(board), "interface_role_match", "sensor")

    assert verdict.status == "warn"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"
    assert "UJA1075ATW" in verdict.detail
    assert "not checked" in verdict.detail


def test_a_passive_part_with_no_interfaces_is_left_alone():
    """A regulator is legitimately `passive`. There is nothing on it for R2 to check,
    and warning about every one of them would bury the case above."""
    board = usb_board(
        regulator=parts.ap2112k(role="passive", interfaces=()),
        loads={"mcu": parts.esp32s3()},
    )

    verdicts = [
        v
        for v in rules.interface_role_match(board)
        if v.subject == "regulator" and "not checked" in (v.detail or "")
    ]

    assert verdicts == []


def test_an_rs485_transceiver_is_satisfied_by_a_uart_controller():
    """A transceiver's advertised bus is its *line* side; what it needs is a UART.

    Equality alone failed every RS-485 board with a conflict no swap could clear, because
    every replacement advertises RS-485 too.
    """
    transceiver = parts.sht40(
        mpn="SP3485EN-L/TR", interfaces=("RS-485", "RS-422"), role="peripheral"
    )
    board = usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(interfaces=("I2C", "SPI", "UART")), "sensor": transceiver},
    )

    verdict = only(rules.interface_role_match(board), "interface_role_match", "sensor")

    assert verdict.status == "pass"


def test_a_uart_peripheral_is_not_satisfied_by_an_rs485_line():
    """The compatibility is directed. An RS-485 pair is two wires, not a controller."""
    from continuity.engine import buses

    assert buses.master_satisfies_bus("RS-485", "UART") is True
    assert buses.master_satisfies_bus("UART", "RS-485") is False


# ── R10 · energy_budget ───────────────────────────────────────────────────────


def _coin_cell_board(draw: float | None, hours: float | None, capacity: float | None = 225.0):
    """`draw` is `i_peak`, which is what `PartSpec.draw` reads first."""
    requirements = Requirements(lifetime_hours=hours, supply_capacity_mah=capacity)
    return usb_board(
        regulator=parts.ap2112k(),
        loads={"mcu": parts.esp32s3(i_typ=draw, i_peak=draw)},
        requirements=requirements,
    )


def test_a_brief_with_no_lifetime_asks_nothing_of_the_energy_rule():
    board = _coin_cell_board(draw=0.070, hours=None)

    assert rules.energy_budget(board) == []


def test_a_board_that_lasts_flat_out_passes():
    """225 mAh at 0.02 mA continuous is well over a year, so no duty cycle is needed."""
    board = _coin_cell_board(draw=0.00002, hours=8760)

    verdict = only(rules.energy_budget(board), "energy_budget")

    assert verdict.status == "pass"
    assert "against the 1 year asked for" in verdict.detail


def test_a_board_that_cannot_last_flat_out_warns_with_both_figures():
    """The e-paper badge: a 70 mA WiFi module on a coin cell, asked for a year.

    A warning rather than a failure — duty cycling is how these boards are built, and the
    engine does not model it. What it must not do is stay silent, which it used to.
    """
    board = _coin_cell_board(draw=0.070, hours=8760)

    verdict = only(rules.energy_budget(board), "energy_budget")

    assert verdict.status == "warn"
    assert not verdict.unchecked, "every figure was known; this is a genuine warning"
    assert "225 mAh" in verdict.detail
    assert "1 year asked for" in verdict.detail
    assert "duty cycling" in verdict.detail


def test_an_unstated_draw_is_reported_unchecked_rather_than_treated_as_zero():
    board = _coin_cell_board(draw=None, hours=8760)

    verdict = only(rules.energy_budget(board), "energy_budget")

    assert verdict.status == "warn"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"
    assert "no current draw" in verdict.detail


def test_a_supply_with_no_capacity_says_so_rather_than_passing():
    """A wall supply has no capacity, so a lifetime asked of one cannot be answered."""
    board = _coin_cell_board(draw=0.070, hours=8760, capacity=None)

    verdict = only(rules.energy_budget(board), "energy_budget")

    assert verdict.status == "warn"
    assert verdict.unchecked, "a required figure is unpublished, so the rule could not be evaluated"
    assert "no capacity" in verdict.detail
