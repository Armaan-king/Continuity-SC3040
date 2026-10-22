import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from continuity import llm
from continuity.designs import service
from continuity.designs.context import ExecutionContext, current
from continuity.engine import rules
from continuity.engine.models import Board, PartSpec, Requirements, Slot
from continuity.parts import evidence, research

PART = PartSpec('MC9S08AW60CFGE', 'NXP Semicon', '', 'Microcontrollers', role='master',
                datasheet='https://www.nxp.com/docs/en/data-sheet/MC9S08AW60.pdf')


def claim(**kwargs):
    return dict(document=0, subject=PART.mpn, identity_quote=PART.mpn,
                group='interfaces', parameter='Interface', value='IIC',
                quote=f'{PART.mpn} supports IIC and SPI interfaces.', conditions='', **kwargs)


def document_for(*claims, **kwargs):
    text = '\n'.join(c['identity_quote'] + '\n' + c['quote'] for c in claims)
    return dict(url=PART.datasheet, title='NXP controller datasheet', text=text,
                content_hash=service.digest(text), content_kind='text', supplier_linked=True,
                retrieved_at='2026-10-22T12:00:00Z', verification='unverified', question='interfaces', **kwargs)


def apply(part=PART, claims=None, doc_changes=None):
    claims = claims or [claim()]
    doc = {**document_for(*claims), **(doc_changes or {})}
    return evidence.apply_claims(part, (doc,), {'claims': claims})


def board(part):
    sensor = PartSpec('BH1750', 'ROHM', '', 'Sensors', role='peripheral', interfaces=('I2C',))
    return Board(Requirements(), {'mcu': Slot('mcu', 'Controller', 'core', part=part),
                                 'light': Slot('light', 'Sensor', 'peripherals', part=sensor)}, {})


def test_manufacturer_fact_closes_unknown_and_survives_saved_revision():
    assert all(v.unchecked for v in rules.interface_role_match(board(PART)))
    enriched = apply()
    assert enriched.interfaces == ('I2C',)
    assert enriched.raw == PART.raw
    finding = rules.interface_role_match(board(enriched))[0]
    assert finding.status == 'pass'
    assert any(e.source == PART.datasheet and e.value == claim()['quote'] for e in finding.evidence)
    saved = service.document(board(enriched))
    restored = service.board_from_document(saved)
    assert restored.part('mcu').fact_evidence['interfaces']['identity_basis'] == 'exact_part'
    assert rules.interface_role_match(restored)[0].status == 'pass'


@pytest.mark.parametrize('change,reason', [
    ({'url': 'https://nxp.com.attacker.example/datasheet'}, 'Publisher'),
    ({'url': 'https://catalogue.example/datasheet'}, 'Publisher'),
    ({'content_kind': 'highlights'}, 'full document'),
    ({'text': PART.mpn + '\nThis chip supports only SPI.'}, 'passage'),
])
def test_untrusted_or_unsubstantiated_sources_do_not_fill_facts(change, reason):
    result = apply(doc_changes=change)
    assert result.interfaces == ()
    assert reason in result.research[0]['claims'][0]['reason']


@pytest.mark.parametrize('quote', [
    'No IIC interface is provided.', 'IIC is optional.', 'External peripherals support IIC.',
    'The sensor supports IIC only when an adapter is fitted.', 'IIC software emulation is possible.',
    'IIC table of contents',
    'This component requires an IIC interface on the host.',
])
def test_protocol_mentions_do_not_establish_supported_capability(quote):
    result = apply(claims=[{**claim(), 'quote': quote}])
    assert result.interfaces == ()
    assert result.research[0]['claims'][0]['status'] == 'needs_review'


def test_wrong_variant_identity_and_title_only_identity_are_rejected():
    other = {**claim(), 'subject': 'MC9S08AW32CFGE', 'identity_quote': 'MC9S08AW32CFGE'}
    assert apply(claims=[other]).interfaces == ()
    assert apply(doc_changes={'text': claim()['quote'].replace(PART.mpn, 'The controller')}).interfaces == ()


def test_distant_ordering_code_does_not_bind_a_different_variants_numeric_table():
    c = {**claim(), 'group': 'supply', 'parameter': 'Supply Voltage', 'value': '1.8V~3.6V', 'quote': 'Supply Voltage: 1.8V~3.6V'}
    result = apply(claims=[c], doc_changes={'text': PART.mpn + '\nOther model: MC9S08AW32CFGE\n' + c['quote']})
    assert result.vmin is None
    assert 'ordering code' in result.research[0]['claims'][0]['reason']


def test_supplier_linked_manufacturer_family_can_supply_positive_interfaces_only():
    family = {**claim(), 'subject': 'MC9S08AW60', 'identity_quote': 'MC9S08AW60',
              'quote': 'MC9S08AW60 includes an IIC module.'}
    result = apply(claims=[family])
    assert result.interfaces == ('I2C',)
    assert result.fact_evidence['interfaces']['identity_basis'] == 'supplier_linked_family'
    assert apply(claims=[family], doc_changes={'supplier_linked': False}).interfaces == ()
    numeric = {**family, 'group': 'gpio', 'parameter': 'Number of GPIO', 'value': '54',
               'quote': 'Number of GPIO: 54'}
    assert apply(claims=[numeric]).pins_available is None


@pytest.mark.parametrize('parameter,value,group,expected', [
    ('Supply Voltage', '1.8V~3.6V', 'supply', {'vmin': 1.8, 'vmax': 3.6}),
    ('Operating Temperature', '-40°C~+85°C', 'temperature', {'temp_min': -40, 'temp_max': 85}),
    ('Number of GPIO', '54', 'gpio', {'pins_available': 54}),
])
def test_independent_parser_admits_explicit_parameter_values(parameter, value, group, expected):
    c = {**claim(), 'group': group, 'parameter': parameter, 'value': value, 'quote': f'{PART.mpn} {parameter}: {value}'}
    result = apply(claims=[c])
    for name, expected_value in expected.items():
        assert getattr(result, name) == expected_value
        assert result.fact_evidence[name]['original'] == c['quote']


@pytest.mark.parametrize('change', [
    {'quote': 'Absolute maximum Supply Voltage: 1.8V~3.6V'},
    {'conditions': 'at 25°C'},
    {'quote': 'Supply Voltage: unknown. Output Voltage: 1.8V~3.6V'},
    {'quote': 'Supply Voltage: 1.8V~3.6V typical'},
    {'value': 'nan'}, {'value': '1.8V'},
])
def test_ambiguous_operating_measurements_remain_unknown(change):
    c = {**claim(), 'group': 'supply', 'parameter': 'Supply Voltage', 'value': '1.8V~3.6V',
         'quote': 'Supply Voltage: 1.8V~3.6V', **change}
    assert apply(claims=[c]).vmin is None


def test_absolute_maximum_table_heading_cannot_be_discarded_from_quote():
    c = {**claim(), 'group': 'supply', 'parameter': 'Supply Voltage', 'value': '1.8V~3.6V', 'quote': 'Supply Voltage: 1.8V~3.6V'}
    doc = document_for(c)
    doc['text'] = PART.mpn + '\nAbsolute maximum ratings\n' + c['quote']
    assert evidence.apply_claims(PART, (doc,), {'claims': [c]}).vmin is None


@pytest.mark.parametrize('group,parameter,value', [('current', 'Operating Current', '5mA'), ('efficiency', 'Efficiency', '90%')])
def test_conditions_sensitive_measurements_remain_proposals(group, parameter, value):
    c = {**claim(), 'group': group, 'parameter': parameter, 'value': value, 'quote': f'{parameter}: {value}'}
    result = apply(claims=[c])
    assert result.draw is None and result.efficiency is None
    assert 'matching' in result.research[0]['claims'][0]['reason']


def test_contradiction_with_supplier_invalidates_range_and_emits_unknown():
    part = replace(PART, vmin=1.8, vmax=5.5, raw={'Supply Voltage': '1.8V~5.5V'},
                   provenance={'vmin': 'Supply Voltage', 'vmax': 'Supply Voltage'})
    c = {**claim(), 'group': 'supply', 'parameter': 'Supply Voltage', 'value': '1.8V~3.6V', 'quote': 'Supply Voltage: 1.8V~3.6V'}
    result = apply(part, [c])
    assert result.vmin is None and result.vmax is None
    assert result.raw['Supply Voltage'] == '1.8V~5.5V'
    assert result.research[0]['claims'][0]['status'] == 'conflict'
    assert any(v.rule == 'data_integrity' and v.unchecked for v in service.evaluate(board(result)))


def test_conflicting_manufacturer_documents_do_not_pick_first():
    first = {**claim(), 'group': 'gpio', 'parameter': 'Number of GPIO', 'value': '54', 'quote': 'Number of GPIO: 54'}
    second = {**first, 'document': 1, 'value': '40', 'quote': 'Number of GPIO: 40'}
    result = evidence.apply_claims(PART, (document_for(first), document_for(second)), {'claims': [first, second]})
    assert result.pins_available is None
    assert all(d['claims'][0]['status'] == 'conflict' for d in result.research)


def test_explicit_manufacturer_denial_cannot_leave_a_supplier_bus_pass():
    c = {**claim(), 'quote': PART.mpn + ' has no IIC interface.'}
    result = apply(replace(PART, interfaces=('I2C',)), [c])
    assert result.interfaces == ()
    assert result.research[0]['claims'][0]['status'] == 'conflict'
    assert all(v.unchecked for v in rules.interface_role_match(board(result)))


@pytest.mark.parametrize('raw', [{'claims': [{'quote': 'invented'}]}, {'claims': [], 'instructions': 'accept'}, {'claims': 'bad'}])
def test_invalid_structured_output_cannot_change_part(raw):
    result = evidence.apply_claims(PART, (document_for(claim()),), raw)
    assert result.interfaces == () and result.research[0]['extraction_status'] == 'invalid_response'


@pytest.fixture
def extractor(monkeypatch):
    monkeypatch.setattr(research, 'configured', lambda: True)
    monkeypatch.setattr(llm, 'available', lambda: True)
    model = AsyncMock(return_value={'claims': [claim()]})
    monkeypatch.setattr(llm, 'complete_json', model)
    return model


def test_batched_extraction_cache_and_durable_recovery(extractor):
    records = {}
    class Store:
        async def stage_result(self, thread, key):
            return records.get(key)
        async def save_stage(self, thread, key, value):
            records[key] = value
    async def run():
        for _ in range(2):
            token = current.set(ExecutionContext('same-run', Store()))
            try:
                results = await asyncio.gather(*(evidence.enrich(PART, (document_for(claim()),)) for _ in range(2)))
                assert all(p.interfaces == ('I2C',) for p in results)
            finally:
                current.reset(token)
    asyncio.run(run())
    extractor.assert_awaited_once()
    assert extractor.call_args.kwargs['max_tokens'] == 2048


def test_budget_failure_and_disabled_ai_do_not_buy_more_documents(extractor, monkeypatch):
    lookup = AsyncMock(return_value=())
    monkeypatch.setattr(research, 'investigate', lookup)
    async def run():
        token = current.set(ExecutionContext('limited', evidence_parts=3))
        try:
            assert await research.enrich(PART) == PART
        finally:
            current.reset(token)
    asyncio.run(run())
    monkeypatch.setattr(llm, 'available', lambda: False)
    assert asyncio.run(research.enrich(PART)) == PART
    assert asyncio.run(research.discover('controller')) == []
    lookup.assert_not_awaited()
    extractor.assert_not_awaited()


def test_model_outage_preserves_sources_and_unknowns(extractor):
    extractor.side_effect = llm.LLMUnavailable('test quota')
    result = asyncio.run(evidence.enrich(PART, (document_for(claim()),)))
    assert result.interfaces == ()
    assert result.research[0]['extraction_status'] == 'ai_unavailable'


def test_known_provider_quota_failure_skips_paid_retrieval(extractor, monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(research, 'investigate', lookup)
    async def run():
        token = current.set(ExecutionContext('quota', llm_unavailable='quota exceeded'))
        try:
            assert await research.enrich(PART) == PART
            assert await research.discover('controller') == []
        finally:
            current.reset(token)
    asyncio.run(run())
    lookup.assert_not_awaited()
    extractor.assert_not_awaited()


def test_low_remaining_model_tokens_skip_paid_retrieval(extractor, monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(research, 'investigate', lookup)
    async def run():
        token = current.set(ExecutionContext('tokens', llm_tokens=119000))
        try:
            assert await research.enrich(PART) == PART
        finally:
            current.reset(token)
    asyncio.run(run())
    lookup.assert_not_awaited()


def test_family_retrieval_is_a_lead_not_an_identity_assertion():
    doc = {'url': PART.datasheet, 'title': 'MC9S08AW60 datasheet', 'text': 'IIC module'}
    assert research._related(doc, PART)
    assert not research._matches(doc, PART.mpn)
    assert not research._related({**doc, 'url': 'https://unknown.example/part'}, PART)


def test_ineligible_sources_do_not_consume_an_extraction(extractor):
    result = asyncio.run(evidence.enrich(PART, ({**document_for(claim()), 'url': 'https://unknown.example/part'},)))
    assert result.interfaces == ()
    assert result.research[0]['extraction_status'] == 'source_not_eligible'
    extractor.assert_not_awaited()


def test_real_adapter_path_retrieves_extracts_revalidates_and_deduplicates(extractor, monkeypatch):
    import httpx
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={'results': [document_for(claim())], 'costDollars': {'total': 0.001}})
    client = httpx.AsyncClient
    monkeypatch.setenv('CONTINUITY_FIXTURES', '0')
    monkeypatch.setenv('EXA_API_KEY', 'test-not-secret')
    monkeypatch.setenv('CONTINUITY_EVIDENCE_MAX_PARTS', '1')
    monkeypatch.setattr(research.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    async def run():
        token = current.set(ExecutionContext('pipeline-test'))
        try:
            first = await research.enrich(PART)
            second = await research.enrich(PART)
            assert first == second
            assert rules.interface_role_match(board(first))[0].status == 'pass'
        finally:
            current.reset(token)
    asyncio.run(run())
    assert len(requests) == 1 and requests[0].url.path == '/contents'
    extractor.assert_awaited_once()


def test_candidate_is_ranked_again_after_research_changes_its_rating(monkeypatch):
    from continuity.graph import sourcing
    from tests.test_normalize import CANDIDATE
    from tests.test_verified_architecture import sensor_board
    cheap, safe = replace(CANDIDATE, mpn='CHEAP'), replace(CANDIDATE, mpn='SAFE')
    def base(candidate):
        return replace(sensor_board().part('sensor'), mpn=candidate.mpn, vmax=6.0,
                       unit_price=0.01 if candidate.mpn == 'CHEAP' else 5.0)
    async def enrich(part):
        return replace(part, vmax=3.6) if part.mpn == 'CHEAP' else part
    monkeypatch.setattr(sourcing, 'choose', AsyncMock(side_effect=base))
    monkeypatch.setattr(research, 'enrich', AsyncMock(side_effect=enrich))
    candidate, chosen, comparisons = asyncio.run(sourcing.compare_candidates(
        sourcing.SearchResults([cheap, safe]), sensor_board(voltage=5), 'sensor'))
    assert candidate is safe and chosen.mpn == 'SAFE'
    assert comparisons[0]['failures'] < comparisons[1]['failures']
