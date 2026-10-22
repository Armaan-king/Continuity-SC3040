"""Regression: a missing supplier interface field is not proof of missing I2C."""
import asyncio
from dataclasses import asdict, replace
from unittest.mock import AsyncMock

import pytest

from continuity.designs import service
from continuity.designs.context import ExecutionContext, current
from continuity.designs.guidance import review_guidance
from continuity.engine import rules
from continuity.engine.models import Board, PartSpec, Port, Requirements, SignalNet, Slot
from continuity.graph.nodes import after_validate
from continuity.graph.sourcing import choose as choose_candidate
from continuity.parts import normalize, research, search, specifications


# Exact specification fields returned for C5193889 on 22 October 2026. The
# manufacturer's datasheet lists IIC, but this supplier payload omits interfaces.
MCU = search.Candidate(
    lcsc='C5193889', mpn='MC9S08AW60CFGE', manufacturer='NXP Semicon',
    description='Microcontrollers (MCU/MPU/SOC)', package='SOT-389-2',
    category='Embedded Processors & Controllers', subcategory='Microcontrollers (MCU/MPU/SOC)',
    stock=10, unit_price=16.3044, library_type='extended',
    specs={'Clock Rate': '20MHz', 'Operating Temperature': '-40℃~+85℃',
           'Features': 'Interrupt response、Integrated ADC interface、Integrated PWM control、RTC and timer、Low-power mode、High-speed peripheral interface、Secure storage and protection'})


def board_with_controller(interfaces=()):
    controller = PartSpec(MCU.mpn, MCU.manufacturer, '', MCU.category, role='master', interfaces=interfaces)
    sensor = PartSpec('BH1750', 'ROHM', '', 'Sensors', role='peripheral', interfaces=('I2C',))
    return Board(Requirements(), {'mcu': Slot('mcu', 'Controller', 'core', part=controller),
                                 'light': Slot('light', 'Light sensor', 'peripherals', part=sensor)}, {})


@pytest.fixture
def offline_lookup(monkeypatch):
    monkeypatch.setattr(search, 'enrich', AsyncMock(return_value=search.Enrichment()))
    monkeypatch.setattr(search, 'live_stock', AsyncMock(return_value=None))
    monkeypatch.setattr(search, 'pinout', AsyncMock(return_value=[]))


def test_exact_mcu_supplier_omission_stays_unknown_and_does_not_request_a_waiver(offline_lookup):
    part = asyncio.run(normalize.normalize(MCU, use_cache=False, research_missing=False))
    assert part.role == 'master' and part.interfaces == ()
    original = board_with_controller()
    board = replace(original, slots={**original.slots, 'mcu': replace(original.slots['mcu'], part=part)})
    findings = rules.interface_role_match(board)
    assert len(findings) == 2
    assert all(f.unchecked and f.status == 'warn' for f in findings)
    assert 'missing' in next(f.detail for f in findings if f.subject == 'light')
    assert after_validate({'verdicts': findings, 'pending': []}) == 'finalize'
    document = service.document(board, verdicts=findings)
    assert document['report']['counts']['fail'] == 0
    assert document['report']['counts']['unknown'] == 2
    assert any('mcu' in task['subjects'] for task in review_guidance(document)['tasks'])


@pytest.mark.parametrize('name', ['Interface', 'Interface Type', 'Connectivity', 'Serial Interface', 'Communication Interface'])
@pytest.mark.parametrize('value', ['IIC', 'I²C', 'I₂C', 'I 2 C', 'I-2-C', 'Ｉ２Ｃ', 'TWI', 'i2c'])
def test_explicit_supplier_i2c_aliases_keep_their_source(name, value):
    parsed, provenance = specifications.parse({name: value})
    assert parsed['interfaces'] == ('I2C',)
    assert provenance['interfaces'] == name


@pytest.mark.parametrize(('value', 'expected'), [
    ('IIC、SPI、I²C', ('I2C', 'SPI')),
    ('SMBus / UART', ('SMBUS', 'UART')),
    ('1-Wire, Single Bus', ('1-WIRE',)),
    ('RS485, RS-422, LIN', ('RS-485', 'RS-422', 'LIN')),
    ('RS–485, CAN‑FD, USART', ('RS-485', 'CAN-FD', 'USART')),
    ('I2S, I²S', ('I2S',)),
    ('SPI, no I2C', ('SPI',)),
    ('No I2C', ()), ('I2C not supported', ()), ('I2C optional', ()),
    ('High-speed peripheral interface', ()), ('N/A', ()),
    ('I2G, SP1', ()),
    ('CAN-XL', ()),
])
def test_interface_tokens_are_explicit_and_not_negated(value, expected):
    parsed, _ = specifications.parse({'Interface': value})
    assert parsed.get('interfaces', ()) == expected


def test_iic_evidence_survives_normalization_and_satisfies_the_sensor(offline_lookup):
    candidate = replace(MCU, specs={**MCU.specs, 'Interface': 'IIC、SPI'})
    part = asyncio.run(normalize.normalize(candidate, use_cache=False, research_missing=False))
    board = board_with_controller()
    board = replace(board, slots={**board.slots, 'mcu': replace(board.slots['mcu'], part=part)})
    result = rules.interface_role_match(board)
    assert len(result) == 1 and result[0].status == 'pass'
    assert any(item.field == 'Interface' and item.value == 'IIC、SPI' for item in result[0].evidence)
    assert part.fact_evidence['interfaces']['original'] == 'IIC、SPI'


def test_known_mismatch_still_fails_and_unknown_controller_is_not_assumed_incompatible():
    board = board_with_controller(('SPI',))
    assert rules.interface_role_match(board)[0].failed
    other = replace(board.part('mcu'), mpn='Unspecified controller', interfaces=())
    board = replace(board, slots={**board.slots, 'other': Slot('other', 'Other controller', 'core', part=other)})
    finding = next(v for v in rules.interface_role_match(board) if v.subject == 'light')
    assert finding.unchecked and not finding.failed
    board = replace(board, slots={**board.slots, 'other': replace(board.slots['other'], part=replace(other, interfaces=('IIC',)))})
    assert next(v for v in rules.interface_role_match(board) if v.subject == 'light').status == 'pass'


def test_declared_net_keeps_missing_controller_capabilities_unknown():
    board = board_with_controller()
    net = SignalNet('light-bus', 'I2C', (Port('mcu', 'I2C', 'controller'), Port('light', 'I2C', 'peripheral')), 'confirmed')
    results = rules.interface_role_match(replace(board, signal_nets=(net,)))
    assert not rules.failures(results)
    assert next(v for v in results if v.subject == 'mcu').unchecked


def test_missing_interface_research_targets_the_gap_before_other_missing_fields(monkeypatch):
    board = board_with_controller()
    missing = research.missing_fields(board.part('mcu'))
    assert 'communication interfaces' in missing[0]
    assert not any('interfaces' in value for value in research.missing_fields(board.part('light')))
    passive = replace(board.part('mcu'), role=None)
    assert not any('interfaces' in value for value in research.missing_fields(passive))
    lookup = AsyncMock(return_value={'documents': []})
    monkeypatch.setattr(research, 'configured', lambda: True)
    monkeypatch.setattr(research, 'search_documents', lookup)
    assert asyncio.run(research.investigate(MCU, missing)) == ()
    query = lookup.call_args.args[0]
    assert MCU.mpn in query and 'communication interfaces' in query


def test_saved_normalization_stage_cannot_reintroduce_the_old_parse(monkeypatch):
    candidate = replace(MCU, specs={'Interface': 'IIC'})
    old_part = board_with_controller().part('mcu')
    old_key = 'normalize_classified_v3:' + service.digest(asdict(candidate))
    saved = {old_key: {'value': asdict(old_part)}}

    class Store:
        async def stage_result(self, thread_id, key):
            return saved.get(key)

        async def save_stage(self, thread_id, key, value):
            saved[key] = value

    normalizer = AsyncMock(return_value=replace(old_part, interfaces=('I2C',)))
    monkeypatch.setattr(normalize, 'normalize', normalizer)

    async def go():
        token = current.set(ExecutionContext('interface-regression', Store()))
        try:
            assert (await choose_candidate(candidate)).interfaces == ('I2C',)
            assert (await choose_candidate(candidate)).interfaces == ('I2C',)
        finally:
            current.reset(token)

    asyncio.run(go())
    normalizer.assert_awaited_once()
    assert len(saved) == 2


def test_usart_support_is_directional_and_can_fd_is_not_assumed_classical_can():
    from continuity.engine.buses import master_satisfies_bus
    assert master_satisfies_bus('UART', 'USART')
    assert not master_satisfies_bus('USART', 'UART')
    assert not master_satisfies_bus('CAN-FD', 'CAN')


@pytest.mark.parametrize(('name', 'value', 'field', 'expected'), [
    ('Voltage – Supply', '3300mV—3600mV', 'vmin', 3.3),
    ('Operating Temperature', '−40℃—+85℃', 'temp_min', -40),
    ('Supply Current', '50μA', 'i_typ', 0.00005),
    ('Supply Current', '50µA', 'i_typ', 0.00005),
    ('Supply Voltage', '１．８Ｖ～３．６Ｖ', 'vmax', 3.6),
])
def test_unicode_measurements_preserve_the_original_source(name, value, field, expected):
    parsed, sources = specifications.parse({name: value})
    assert parsed[field] == pytest.approx(expected)
    assert sources[field] == name


def test_ambiguous_decimal_comma_is_not_silently_reinterpreted():
    assert specifications.measurements('3,3V', 'V') == []
