"""Real SDK requests over a fake HTTP transport; no external provider calls."""
import asyncio
import json
import logging
import subprocess
import sys
from unittest.mock import AsyncMock

import httpx
import pytest

from continuity import ai_activity, llm
from continuity.designs.context import current, ExecutionContext
from tests.test_runtime import clean_environment, groq_transport, json_completion


@pytest.fixture(autouse=True)
def fresh_activity(monkeypatch):
    monkeypatch.setattr(ai_activity, "_identity", None)


def activity():
    return llm.public_status()["activity"]


def test_configured_is_not_success_and_success_is_observable(clean_environment, monkeypatch, caplog):
    groq_transport(monkeypatch, lambda request: json_completion())
    assert activity()["last_request"]["status"] == "not_tested"
    assert activity()["requests"]["attempted"] == 0
    receipts = []
    with caplog.at_level(logging.INFO, logger="continuity.ai_activity"), ai_activity.observe(receipts.append):
        assert asyncio.run(llm.complete_json("private instructions", "private brief")) == {"ok": True}
    assert activity()["requests"] == {"attempted": 1, "succeeded": 1, "failed": 0, "cancelled": 0}
    assert activity()["last_request"]["http_status"] == 200
    assert len(receipts) == 1
    assert "ai_request started" in caplog.text
    assert "ai_request success" in caplog.text
    for secret in ("test-groq-secret", "private instructions", "private brief"):
        assert secret not in caplog.text + json.dumps(activity()) + json.dumps(receipts)


@pytest.mark.parametrize("status", [400, 401, 403, 429, 503])
def test_http_failure_has_receipt_and_never_claims_success(clean_environment, monkeypatch, status):
    groq_transport(monkeypatch, lambda request: httpx.Response(status, json={
        "error": {"message": "private brief test-groq-secret", "failed_generation": "private output"}}))
    receipts = []
    with ai_activity.observe(receipts.append), pytest.raises(llm.LLMUnavailable):
        asyncio.run(llm.complete_json("private instructions", "private brief", max_attempts=1))
    assert activity()["requests"] == {"attempted": 1, "succeeded": 0, "failed": 1, "cancelled": 0}
    assert receipts[0]["http_status"] == status
    assert ai_activity.message(receipts[0])[1] == "warning"
    for secret in ("test-groq-secret", "private brief", "private output"):
        assert secret not in json.dumps(receipts) + json.dumps(activity())


def test_invalid_json_is_distinct_from_http_failure(clean_environment, monkeypatch):
    response = json_completion().json()
    response["choices"][0]["message"]["content"] = "private malformed output"
    groq_transport(monkeypatch, lambda request: httpx.Response(200, json=response))
    with pytest.raises(llm.LLMUnavailable):
        asyncio.run(llm.complete_json("Test", "Hello", max_attempts=1))
    receipt = activity()["last_request"]
    assert receipt["status"] == "error" and receipt["http_status"] == 200
    assert "valid JSON" in receipt["reason"]


def test_cache_hit_is_labelled_without_inventing_another_request(clean_environment, monkeypatch):
    calls, receipts = [], []
    groq_transport(monkeypatch, lambda request: calls.append(request) or json_completion())
    async def go():
        token = current.set(ExecutionContext("private-thread"))
        try:
            with ai_activity.observe(receipts.append):
                await llm.complete_json("Test", "Hello")
                await llm.complete_json("Test", "Hello")
        finally:
            current.reset(token)
    asyncio.run(go())
    assert len(calls) == 1
    assert [receipt["status"] for receipt in receipts] == ["success", "cached"]
    assert activity()["requests"]["attempted"] == 1
    assert "private-thread" not in json.dumps(activity())


def test_key_change_does_not_reuse_previous_success(clean_environment, monkeypatch):
    groq_transport(monkeypatch, lambda request: json_completion())
    asyncio.run(llm.complete_json("Test", "Hello"))
    monkeypatch.setenv("GROQ_API_KEY", "different-test-key")
    assert activity()["last_request"]["status"] == "not_tested"
    assert activity()["requests"]["attempted"] == 0


def test_retry_records_both_outcomes_without_counting_cache_hits(clean_environment, monkeypatch):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(503, json={"error": {"message": "unavailable"}}) if len(calls) == 1 else json_completion()
    groq_transport(monkeypatch, respond)
    monkeypatch.setattr(llm.asyncio, "sleep", AsyncMock())
    asyncio.run(llm.complete_json("Test", "Hello"))
    assert activity()["requests"] == {"attempted": 2, "succeeded": 1, "failed": 1, "cancelled": 0}


def test_cancelled_request_is_not_reported_as_success(clean_environment, monkeypatch):
    async def respond(request):
        raise asyncio.CancelledError()
    groq_transport(monkeypatch, respond)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(llm.complete_json("Test", "Hello"))
    assert activity()["requests"] == {"attempted": 1, "succeeded": 0, "failed": 0, "cancelled": 1}


def test_broken_observer_cannot_retry_a_successful_request(clean_environment, monkeypatch):
    calls = []
    groq_transport(monkeypatch, lambda request: calls.append(request) or json_completion())
    def broken(receipt):
        raise ValueError("observer failed")
    with ai_activity.observe(broken):
        assert asyncio.run(llm.complete_json("Test", "Hello")) == {"ok": True}
    assert len(calls) == 1 and activity()["requests"]["succeeded"] == 1


def test_application_logging_is_enabled_under_uvicorn_without_sdk_debug():
    code = """
import logging
from uvicorn.config import Config
Config('continuity.server:app').configure_logging()
from continuity.logging_setup import configure
configure()
configure()
logging.getLogger('continuity.ai_activity').info('ai_request success request_id=test')
logging.getLogger('httpx').info('HTTP details must not be enabled')
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stderr.count("ai_request success") == 1
    assert "HTTP details" not in result.stderr


@pytest.mark.parametrize("failure", [False, True])
def test_provider_receipts_reach_actual_design_sse(clean_environment, monkeypatch, failure):
    from continuity.api import app as app_module
    from continuity.api.runs import RunManager
    from continuity.graph import sourcing
    from continuity.graph.build import build
    from tests.test_api import stream
    from tests.test_planner import GOOD

    calls = []
    def respond(request):
        calls.append(request)
        if failure:
            return httpx.Response(403, json={"error": {"code": "model_permission_blocked", "message": "private"}})
        response = json_completion().json()
        response["choices"][0]["message"]["content"] = json.dumps(GOOD)
        response["usage"] = {"total_tokens": 123, "prompt_tokens": 100, "completion_tokens": 23}
        return httpx.Response(200, json=response)
    groq_transport(monkeypatch, respond)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(sourcing, "find", AsyncMock(return_value=[]))
    monkeypatch.setattr(app_module.app.state, "graph", build())
    monkeypatch.setattr(app_module.app.state, "store", None)
    monkeypatch.setattr(app_module.app.state, "runs", RunManager())
    frames = asyncio.run(stream("/design", {"prompt": "USB powered motion monitor"}))
    receipts = [frame for frame in frames if "ai" in frame]
    assert len(calls) == 1 and len(receipts) == 1
    assert receipts[0]["ai"]["status"] == ("error" if failure else "success")
    assert receipts[0]["type"] == "reasoning"
    seqs = [frame["seq"] for frame in frames]
    assert seqs == sorted(set(seqs))
    texts = [frame["text"] for frame in frames if frame["type"] == "reasoning"]
    assert any("AI planning is unavailable" in text for text in texts) is failure
    assert any("AI plan accepted" in text for text in texts) is not failure
    assert any("Supplier mode: live" in text for text in texts)
    if not failure:
        assert "123 tokens" in receipts[0]["text"]


@pytest.mark.parametrize("value,expected", [("1", "recorded"), ("true", "live"), ("0", "live")])
def test_runtime_reports_the_mode_the_supplier_client_actually_uses(clean_environment, monkeypatch, value, expected):
    from continuity.api.app import app
    monkeypatch.setenv("CONTINUITY_FIXTURES", value)
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/runtime")
    assert asyncio.run(go()).json()["parts"] == expected


@pytest.mark.parametrize("recorded", [False, True])
def test_supplier_client_uses_only_the_selected_source(clean_environment, monkeypatch, tmp_path, recorded, caplog):
    from continuity.parts import fixtures, mcp
    monkeypatch.setenv("CONTINUITY_FIXTURES", "1" if recorded else "0")
    monkeypatch.setattr(fixtures, "FIXTURE_DIR", tmp_path)
    fixtures.save("jlc_search", {"query": "sensor"}, {"source": "recorded"})
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"result": {"content": [
            {"type": "text", "text": '{"source":"live"}'}]}})
    client_type = httpx.AsyncClient
    monkeypatch.setattr(mcp.httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(mcp._limiter, "wait", AsyncMock())
    with caplog.at_level(logging.INFO, logger="continuity.parts.mcp"):
        result = asyncio.run(mcp.call_tool("jlc_search", {"query": "sensor"}))
    mode = "recorded" if recorded else "live"
    assert result == {"source": mode}
    assert len(calls) == (0 if recorded else 1)
    assert f"supplier_request mode={mode}" in caplog.text
