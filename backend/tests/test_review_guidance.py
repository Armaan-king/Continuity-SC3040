"""Regression cases from the bicycle alarm report and bounded provider orchestration."""
import asyncio
import copy
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest

from continuity import llm
from continuity.designs import service
from continuity.designs.context import ExecutionContext, current
from continuity.designs.guidance import review_guidance
from continuity.engine.models import Board, PartSpec, Rail, Requirements, Slot
from continuity.engine import rules
from continuity.planner import plan as planner
from continuity.planner.requirements import explicit_requirements
from continuity.parts import research
from tests.test_planner import GOOD
from tests.test_runtime import groq_transport, json_completion, clean_environment

BRIEF = ('Design a battery-powered bicycle alarm with accelerometer, buzzer, button and LED. '
         'Power it from a single rechargeable 3.7 V Li-ion battery with USB-C charging. Prioritize compact size.')


def bicycle_document():
    regulator = PartSpec('BUCK', 'Test', 'Fixture', 'DC-DC Converters', vmin=4.5, vmax=40,
                         vout_min=1.23, vout_max=37, i_max=2, topology='buck', role='passive')
    mcu = PartSpec('MCU', 'Test', 'Fixture', 'MCU', vmin=1.7, vmax=3.6, role='master')
    return service.document(Board(Requirements(input_source='usb-5v+liion'),
        {'regulator': Slot('regulator', 'Regulator', 'power', part=regulator),
         'mcu': Slot('mcu', 'Controller', 'core', part=mcu)},
        {'VIN': Rail('VIN', 5, members=('regulator',), i_limit=1.5),
         '3V3': Rail('3V3', 3.3, 'regulator', ('mcu',))}), prompt=BRIEF)


def test_zero_failures_is_not_battery_validation_and_guidance_does_not_mutate_snapshot():
    document = bicycle_document()
    before = copy.deepcopy(document)
    guidance = review_guidance(document)
    assert document == before
    assert guidance['status'] == 'needs_review'
    assert 'battery-modes' in {t['id'] for t in guidance['tasks']}
    questions = {q['field']: q for q in guidance['questions']}
    assert questions['lifetime_hours']['value'] is None
    assert questions['supply_capacity_mah']['value'] is None
    assert 'max_package_mm' in questions
    assert all(t['fields'] for t in guidance['tasks'] if t['kind'] == 'evidence')


def test_confirmed_answers_leave_missing_component_evidence_open():
    document = bicycle_document()
    board = service.board_from_document(document)
    updated = service.document(replace(board, requirements=replace(board.requirements, lifetime_hours=72, supply_capacity_mah=800)),
        prompt=BRIEF, confirmed=['lifetime_hours', 'supply_capacity_mah'])
    guidance = review_guidance(updated)
    assert not {'lifetime_hours', 'supply_capacity_mah'} & {q['field'] for q in guidance['questions']}
    assert guidance['status'] == 'needs_review'
    assert any(f['outcome'] == 'UNKNOWN' and f['rule'] == 'energy_budget' for f in updated['report']['findings'])


def test_no_current_data_never_displays_zero_load_or_full_headroom():
    document = bicycle_document()
    current_findings = [f for f in document['report']['findings'] if f['rule'] == 'current_budget']
    assert len(current_findings) == 2
    assert all(f['outcome'] == 'UNKNOWN' for f in current_findings)
    assert all(f['detail'].startswith('Load on ') for f in current_findings)
    assert all('leaving 100%' not in f['detail'] and 'Estimated 0' not in f['detail'] for f in current_findings)


def test_known_overload_still_fails_with_missing_loads():
    board = service.board_from_document(bicycle_document())
    slots = {**board.slots, 'heavy': Slot('heavy', 'Heavy load', 'peripherals', part=PartSpec('HEAVY', 'Test', '', '', i_peak=3))}
    rails = {**board.rails, '3V3': replace(board.rails['3V3'], members=('mcu', 'heavy'))}
    results = rules.current_budget(replace(board, slots=slots, rails=rails))
    assert any(v.failed and v.scope == '3V3' for v in results)


def test_battery_brief_keeps_battery_operation_and_capacity_is_never_defaulted():
    explicit = explicit_requirements(BRIEF)
    assert explicit['input_source'] == 'battery-3v7'
    assert explicit['input_voltage'] == 3.7
    assert planner.fallback_plan(BRIEF).requirements.supply_capacity_mah is None
    assert planner.fallback_plan(BRIEF + ' Use a 1200mAh cell.').requirements.supply_capacity_mah == 1200


def test_ai_cannot_override_explicit_supply_or_invent_battery_capacity(monkeypatch):
    monkeypatch.setattr(llm, 'available', lambda: True)
    monkeypatch.setattr(llm, 'complete_json', AsyncMock(return_value={**GOOD, 'input_source': 'usb-5v', 'input_voltage': 5, 'supply_capacity_mah': 2000}))
    plan = asyncio.run(planner.plan_board('A battery-powered sensor using a 3.7 V Li-ion battery.'))
    assert plan.requirements.input_voltage == 3.7
    assert plan.requirements.input_source == 'battery-3v7'
    assert plan.requirements.supply_capacity_mah is None
    assert plan.slots['regulator'].constraint['vin_min'] == 3.7


def test_charger_termination_cannot_be_used_as_battery_operating_rail():
    payload = {**GOOD, 'input_source': 'battery-3v7', 'slots': [{**GOOD['slots'][0], 'category': 'charger'}, *GOOD['slots'][1:]]}
    assert any('constant supply' in issue for issue in planner.structural_issues(planner.build_plan(payload)))


def test_missing_explicit_support_functions_are_rejected_and_fallback_keeps_them():
    assert len([issue for issue in planner.structural_issues(planner.build_plan(GOOD), BRIEF) if 'separate' in issue]) == 4
    fallback = planner.fallback_plan(BRIEF)
    assert {'button', 'led', 'buzzer', 'charger'} <= fallback.slots.keys()
    assert fallback.slots['button'].tier == 'passives'
    assert 'charger' not in fallback.rails[0].members
    assert not any(target in {'charger', 'button', 'led', 'buzzer'} for _, target in fallback.links)
    assert not planner.requested_functions('A sensor without a button and no LED')


def test_concurrent_identical_completions_are_coalesced_and_results_are_not_shared(clean_environment, monkeypatch):
    requests = []
    groq_transport(monkeypatch, lambda request: requests.append(request) or json_completion())
    async def go():
        token = current.set(ExecutionContext('test-cache'))
        try:
            a, b = await asyncio.gather(llm.complete_json('system', 'input'), llm.complete_json('system', 'input'))
            a['ok'] = False
            assert b['ok'] is True
            assert (await llm.complete_json('system', 'input'))['ok'] is True
        finally:
            current.reset(token)
    asyncio.run(go())
    assert len(requests) == 1


@pytest.mark.parametrize('setting,value', [('CONTINUITY_LLM_MAX_CALLS', '1'), ('CONTINUITY_LLM_TOKEN_BUDGET', '5000')])
def test_model_run_budgets_stop_new_requests(clean_environment, monkeypatch, setting, value):
    requests = []
    groq_transport(monkeypatch, lambda request: requests.append(request) or json_completion())
    monkeypatch.setenv(setting, value)
    async def go():
        token = current.set(ExecutionContext('test-budget'))
        try:
            await llm.complete_json('system', 'first')
            with pytest.raises(llm.LLMUnavailable, match='budget'):
                await llm.complete_json('system', 'second')
        finally:
            current.reset(token)
    asyncio.run(go())
    assert len(requests) == 1


def test_exhausted_provider_quota_does_not_repeat_for_every_part(clean_environment, monkeypatch):
    requests = []
    groq_transport(monkeypatch, lambda request: requests.append(request) or httpx.Response(429, headers={'retry-after': '60'}, json={'error': {'message': 'quota'}}))
    async def go():
        token = current.set(ExecutionContext('test-quota'))
        try:
            for text in ['planning', 'normalization', 'review']:
                with pytest.raises(llm.LLMUnavailable, match='quota'):
                    await llm.complete_json('system', text)
        finally:
            current.reset(token)
    asyncio.run(go())
    assert len(requests) == 1


def test_groq_planner_uses_strict_schema_for_tiers_and_categories(clean_environment, monkeypatch):
    import json
    from continuity.planner.schema import PLAN_SCHEMA
    requests = []
    groq_transport(monkeypatch, lambda request: requests.append(request) or json_completion())
    asyncio.run(llm.complete_json('system', 'input', schema=PLAN_SCHEMA))
    output = json.loads(requests[0].content)['response_format']
    assert output['type'] == 'json_schema'
    assert output['json_schema']['strict'] is True
    slot = output['json_schema']['schema']['properties']['slots']['items']
    assert slot['properties']['tier']['enum'] == ['power', 'core', 'peripherals', 'passives']
    assert 'switch' in slot['properties']['category']['enum']


def test_research_prefers_known_datasheet_and_rejects_other_variants(monkeypatch):
    monkeypatch.setattr(research, 'configured', lambda: True)
    read = AsyncMock(return_value={'documents': [{'title': 'MCU datasheet', 'text': 'MCU operating current', 'url': 'https://vendor.example/datasheet'}]})
    search = AsyncMock()
    monkeypatch.setattr(research, 'read_documents', read)
    monkeypatch.setattr(research, 'search_documents', search)
    part = PartSpec('MCU', 'Test', '', '', datasheet='https://vendor.example/datasheet')
    documents = asyncio.run(research.investigate(part, ['i_peak']))
    assert len(documents) == 1
    search.assert_not_awaited()
    assert not research._matches({'title': 'MCU-OTHER', 'text': 'other part'}, 'MCU')
