import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import httpx

from continuity import llm
from continuity.api.revisions import RequirementEdit
from continuity.designs.answer_proposals import propose_answers
from continuity.designs.guidance import review_guidance
from tests.test_review_guidance import bicycle_document
from tests.test_runtime import clean_environment, groq_transport, json_completion


def edit(field='lifetime_hours', value=72, upper=None, quote='Run for 3 days'):
    return dict(field=field, value=value, upper=upper, quote=quote)


def test_interpretation_returns_only_explicit_preview_and_one_completion(monkeypatch):
    prompt = 'Run for 3 days with a 30% current margin.'
    completion = AsyncMock(return_value={'edits': [edit(), edit('current_margin', 0.3, quote='30% current margin')], 'unhandled': []})
    monkeypatch.setattr(llm, 'complete_json', completion)
    result = asyncio.run(propose_answers(prompt, review_guidance(bicycle_document())['questions']))
    assert result['changes'] == {'lifetime_hours': 72, 'current_margin': 0.3}
    completion.assert_awaited_once()
    sent = json.loads(completion.call_args.args[1])
    assert sent['request'] == prompt
    assert all('value' not in question for question in sent['questions'])
    assert 'response' not in result
    RequirementEdit.model_validate({'expected_revision': 'test', **result['changes']})


@pytest.mark.parametrize('edits', [
    [edit(quote='A quote not in the user request')],
    [edit(quote=' ')],
    [edit(), edit()],
    [edit('pins_available', 32)],
    [edit(value=float('nan'))],
    [edit(value=True)],
    [edit(upper=100)],
    [edit('temp_range', -10.5, 60)],
    [edit('temp_range', -10, None)],
])
def test_malformed_or_unsupported_model_edits_are_rejected(monkeypatch, edits):
    monkeypatch.setattr(llm, 'complete_json', AsyncMock(return_value={'edits': edits, 'unhandled': []}))
    with pytest.raises(ValueError):
        asyncio.run(propose_answers('Run for 3 days', review_guidance(bicycle_document())['questions']))


def test_confirmed_fields_cannot_be_changed_by_an_open_question_proposal(monkeypatch):
    monkeypatch.setattr(llm, 'complete_json', AsyncMock(return_value={'edits': [edit()], 'unhandled': []}))
    with pytest.raises(ValueError):
        asyncio.run(propose_answers('Run for 3 days', []))


def test_unsupported_request_stays_open(monkeypatch):
    monkeypatch.setattr(llm, 'complete_json', AsyncMock(return_value={
        'edits': [], 'unhandled': ['Component replacement requires the workspace.']}))
    result = asyncio.run(propose_answers('Replace the buzzer', review_guidance(bicycle_document())['questions']))
    assert result['changes'] == {}
    assert result['unhandled']


def test_groq_preview_uses_the_strict_schema(clean_environment, monkeypatch):
    calls = []
    groq_transport(monkeypatch, lambda request: calls.append(request) or json_completion())
    # Transport stub returns a different schema; inspection still verifies the outbound contract.
    with pytest.raises(ValueError):
        asyncio.run(propose_answers('Run for 3 days', review_guidance(bicycle_document())['questions']))
    fmt = json.loads(calls[0].content)['response_format']
    assert fmt['type'] == 'json_schema'
    assert fmt['json_schema']['strict'] is True
    schema = fmt['json_schema']['schema']
    assert schema['additionalProperties'] is False
    assert 'quote' in schema['$defs']['AnswerEdit']['required']


def test_preview_rate_limit_leaves_direct_answers_and_sign_in_available(clean_environment, monkeypatch):
    from continuity.api.security import SecurityMiddleware
    from starlette.responses import JSONResponse
    monkeypatch.setenv('CONTINUITY_ENV', 'production')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused-test-database')

    async def handler(scope, receive, send):
        await JSONResponse({'ok': True})(scope, receive, send)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=SecurityMiddleware(handler)), base_url='http://test') as client:
            for _ in range(5):
                assert (await client.post('/revisions/current/answer-proposal')).status_code == 200
            limited = await client.post('/revisions/current/answer-proposal')
            assert limited.status_code == 429
            assert limited.headers['Retry-After'] == '60'
            assert (await client.post('/revisions/current/requirements')).status_code == 200
            assert (await client.post('/auth/login')).status_code == 200
    asyncio.run(go())
