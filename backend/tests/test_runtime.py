"""Runtime contracts, provider selection, and conservative no-key extraction."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from continuity import llm
from continuity.api.app import app
from continuity.parts.specifications import parse, measurements
from continuity.settings import Settings


@pytest.fixture
def clean_environment(monkeypatch):
    import os
    for key in tuple(os.environ):
        if key.startswith("CONTINUITY_") or key in {"DATABASE_URL", "GROQ_API_KEY", "GEMINI_API_KEY", "ZAI_API_KEY", "OLLAMA_API_KEY"}:
            monkeypatch.delenv(key)


def test_missing_keys_explicitly_select_rules(clean_environment):
    assert llm.public_status() == {"configured": False, "provider": "disabled", "model": None, "mode": "rules"}


def test_native_groq_key_selects_compatible_endpoint(clean_environment, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-secret")
    config = llm.configuration()
    assert config.enabled
    assert config.provider == "groq"
    assert config.base_url == "https://api.groq.com/openai/v1"
    assert config.model == "openai/gpt-oss-120b"
    assert "test-secret" not in str(llm.public_status())


def test_ollama_needs_no_paid_key(clean_environment, monkeypatch):
    monkeypatch.setenv("CONTINUITY_LLM_PROVIDER", "ollama")
    assert llm.configuration().enabled
    assert llm.configuration().key == "ollama"


def test_production_requires_persistence(clean_environment, monkeypatch):
    monkeypatch.setenv("CONTINUITY_ENV", "production")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings.read()


def test_invalid_cross_site_cookie_rejected(clean_environment, monkeypatch):
    monkeypatch.setenv("CONTINUITY_COOKIE_SAMESITE", "none")
    with pytest.raises(ValueError, match="requires"):
        Settings.read()


def test_compatible_completion_sets_json_mode_and_token_budget(clean_environment, monkeypatch):
    monkeypatch.setenv("CONTINUITY_LLM_PROVIDER", "ollama")
    create = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))], usage=None))
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.chat.completions.create = create
    import openai
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: client)
    assert asyncio.run(llm.complete_json("Test", "Hello")) == {"ok": True}
    assert create.call_args.kwargs["response_format"] == {"type": "json_object"}
    assert create.call_args.kwargs["max_tokens"] == 4096
    assert "reasoning_effort" not in create.call_args.kwargs


@pytest.mark.parametrize("provider, model_override", [
    ("auto", None), ("gemini", None), ("gemini", "deployment-model"),
])
def test_gemini_request_uses_native_key_endpoint_and_selected_model(
    clean_environment, monkeypatch, provider, model_override,
):
    import openai
    client_type = openai.AsyncOpenAI
    monkeypatch.setenv("CONTINUITY_LLM_PROVIDER", provider)
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-secret")
    if model_override:
        monkeypatch.setenv("CONTINUITY_LLM_MODEL", model_override)
        monkeypatch.setenv("CONTINUITY_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
    expected_model = model_override or "gemini-3.8-flash"
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-gemini-secret"
        payload = json.loads(request.content)
        assert payload["model"] == expected_model
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["max_tokens"] == 4096
        return httpx.Response(200, json={
            "id": "test-completion", "object": "chat.completion", "created": 0,
            "model": expected_model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": '{"ok":true}'}}],
        })

    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: client_type(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))))
    assert asyncio.run(llm.complete_json("Test", "Hello")) == {"ok": True}
    assert len(requests) == 1
    assert llm.public_status() == {
        "configured": True, "provider": "gemini", "model": expected_model, "mode": "ai",
    }


def groq_transport(monkeypatch, respond):
    import openai
    client_type = openai.AsyncOpenAI
    monkeypatch.setenv("CONTINUITY_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-secret")
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: client_type(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))))


def json_completion():
    return httpx.Response(200, json={
        "id": "test-completion", "object": "chat.completion", "created": 0,
        "model": "openai/gpt-oss-120b", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": '{"ok":true}'}}],
    })


@pytest.mark.parametrize("model,effort,expected", [
    ("openai/gpt-oss-120b", "minimal", "low"),
    ("openai/gpt-oss-20b", "medium", "medium"),
    ("another-groq-model", "low", None),
])
def test_groq_request_uses_native_key_json_and_supported_reasoning(clean_environment, monkeypatch, model, effort, expected):
    monkeypatch.setenv("CONTINUITY_LLM_API_KEY", "stale-other-provider-key")
    monkeypatch.setenv("GEMINI_API_KEY", "unused-gemini-key")
    monkeypatch.setenv("CONTINUITY_LLM_MODEL", model)
    requests = []
    def respond(request):
        requests.append(request)
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-groq-secret"
        payload = json.loads(request.content)
        assert payload["model"] == model
        assert payload["max_completion_tokens"] == 4096
        assert "max_tokens" not in payload
        assert payload["response_format"] == {"type": "json_object"}
        assert payload.get("reasoning_effort") == expected
        return json_completion()
    groq_transport(monkeypatch, respond)
    assert asyncio.run(llm.complete_json("Test", "Return JSON", effort=effort)) == {"ok": True}
    assert len(requests) == 1


def test_short_provider_cooldown_is_respected(clean_environment, monkeypatch):
    requests = []
    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "3.5"}, json={"error": {"message": "quota"}})
        return json_completion()
    groq_transport(monkeypatch, respond)
    sleep = AsyncMock()
    monkeypatch.setattr(llm.asyncio, "sleep", sleep)
    assert asyncio.run(llm.complete_json("Test", "Hello")) == {"ok": True}
    sleep.assert_awaited_once_with(3.5)
    assert len(requests) == 2


@pytest.mark.parametrize("status,retry_after,reason", [(429, "3600", "quota"), (401, None, "API key"), (404, None, "model ID")])
def test_nonretryable_failure_is_sanitized_and_not_repeated(clean_environment, monkeypatch, status, retry_after, reason):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(status, headers={"Retry-After": retry_after} if retry_after else {},
                              json={"error": {"message": "test-groq-secret provider echoed input"}})
    groq_transport(monkeypatch, respond)
    sleep = AsyncMock()
    monkeypatch.setattr(llm.asyncio, "sleep", sleep)
    with pytest.raises(llm.LLMUnavailable, match=reason) as error:
        asyncio.run(llm.complete_json("Test", "Hello"))
    assert "test-groq-secret" not in str(error.value)
    assert len(requests) == 1
    sleep.assert_not_awaited()


def test_explicit_ranges_and_units_keep_provenance():
    fields, provenance = parse({"Voltage - Supply": "1.8V ~ 3.6V", "Current - Supply": "400nA"})
    assert fields["vmin"] == 1.8
    assert fields["vmax"] == 3.6
    assert fields["i_typ"] == pytest.approx(4e-7)
    assert provenance["i_typ"] == "Current - Supply"


@pytest.mark.parametrize("raw", ["3.3V nominal", "3.3", "up to 5V", "5V ~ 1V", "nanV"])
def test_ambiguous_measurements_remain_unknown(raw):
    assert measurements(raw, "V") == []


def test_nominal_input_and_adjustable_output_do_not_become_operating_ranges():
    fields, _ = parse({"Supply Voltage": "3.3V", "Output Voltage": "3.3V", "Output Type": "Adjustable"})
    assert fields == {"vout_max": 3.3}, "an adjustable ceiling is not a complete output or supply range"


def test_cross_origin_mutation_is_rejected(clean_environment):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/design", json={"prompt": "USB sensor"}, headers={"Origin": "https://untrusted.example"})
    assert asyncio.run(go()).status_code == 403


def test_model_cannot_use_a_microcontroller_as_a_regulator(monkeypatch):
    from continuity.planner import plan as planner
    invalid = {"slots": [{"id": "mcu", "tier": "core"}, {"id": "sensor", "tier": "peripherals"}],
               "rails": [{"id": "3V3", "voltage": 3.3, "source": "mcu", "members": ["sensor"]}]}
    complete = AsyncMock(return_value=invalid)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "complete_json", complete)
    assert asyncio.run(planner.plan_board("USB sensor")) is None
    assert complete.call_count == 2
    assert "separate power slot" in complete.call_args.args[1]


def test_basic_planning_preserves_explicit_operating_requirements():
    from continuity.planner.plan import fallback_plan
    plan = fallback_plan("USB industrial temperature sensor, 5,000 units, must last a year")
    assert plan.requirements.temp_range == (-40, 85)
    assert plan.requirements.min_stock == 5000
    assert plan.requirements.lifetime_hours == 8760


def test_nonfinite_model_measurement_is_unknown():
    from continuity.parts.normalize import validate
    fields, _ = validate({"vmin": float("nan"), "vmax": float("inf")}, {})
    assert "vmin" not in fields and "vmax" not in fields
