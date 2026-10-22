"""Exa HTTP receipts in logs, runtime status and the real design event stream."""
import asyncio
import json
import logging
from unittest.mock import AsyncMock

import httpx
import pytest

from continuity import ai_activity
from continuity.designs.context import ExecutionContext, current
from continuity.parts import research, research_activity
from tests.test_multi_source import providers, run


def test_receipts_separate_requests_cache_and_skips_without_private_content(providers, caplog, monkeypatch):
    requests, _ = providers
    monkeypatch.setenv("CONTINUITY_RESEARCH_MAX_CALLS", "1")
    receipts = []
    async def work():
        await research.search_documents("private brief")
        await research.search_documents("private brief")
        await research.search_documents("another brief")
    with caplog.at_level(logging.INFO, logger="continuity.parts.research_activity"), ai_activity.observe(receipts.append):
        run(work)
    status = research.status()
    activity = status["activity"]
    assert status["sourcing"] == "web_and_catalogue"
    assert activity["requests"] == {"attempted": 1, "succeeded": 1, "failed": 0, "cancelled": 0}
    assert activity["cache_hits"] == 1 and activity["skipped"] == 1
    assert activity["last_request"]["status"] == "success" and activity["last_event"]["status"] == "skipped"
    assert [r["status"] for r in receipts] == ["success", "cached", "skipped"]
    assert "exa_request started" in caplog.text and "exa_request success" in caplog.text
    assert "No API request sent" in ai_activity.message(receipts[-1])[0]
    assert ai_activity.message(receipts[-1])[1] == "warning"
    public = caplog.text + json.dumps(activity) + json.dumps(receipts)
    for secret in ("fake-exa-secret", "private brief", "another brief", "www.nxp.com", "TEST-MCU-42"):
        assert secret not in public
    assert len(requests) == 1


@pytest.mark.parametrize("code", [400, 401, 403, 429, 503])
def test_error_is_visible_and_not_retried(providers, code):
    requests, response = providers
    response.update(http_status=code, error={"message": "fake-exa-secret private content"})
    receipts = []
    async def work():
        first = await research.search_documents("test")
        assert first["error"] == f"http_{code}"
        assert await research.search_documents("test") == first
    with ai_activity.observe(receipts.append):
        run(work)
    assert len(requests) == 1
    assert research_activity.status()["requests"]["failed"] == 1
    assert receipts[0]["http_status"] == code and receipts[1]["reason"] == "cached_failure"
    assert all(ai_activity.message(r)[1] == "warning" for r in receipts)
    assert "private content" not in json.dumps(receipts)


def test_partial_response_reports_usable_count_and_unknown_cost(providers):
    _, response = providers
    response.pop("costDollars")
    response["statuses"] = [{"id": response["results"][0]["url"], "status": "error"}]
    run(lambda: research.read_documents([response["results"][0]["url"]]))
    receipt = research_activity.status()["last_request"]
    assert receipt["status"] == "success" and receipt["partial"] and receipt["documents"] == 0
    assert receipt["cost_usd"] is None
    text, level = ai_activity.message(receipt)
    assert "0 usable" in text and "$" not in text and level == "warning"


def test_empty_results_are_not_counted_as_usable_documents(providers):
    _, response = providers
    response["results"][0]["text"] = ""
    run(lambda: research.search_documents("test"))
    assert research_activity.status()["last_request"]["documents"] == 0


def test_actual_reported_cost_prevents_further_local_spend(providers):
    requests, response = providers
    response["costDollars"]["total"] = 0.115
    async def work():
        await research.search_documents("one")
        assert (await research.search_documents("two"))["error"] == "budget_exhausted"
    run(work)
    assert len(requests) == 1


def test_cancelled_request_is_distinct_from_success(providers, monkeypatch):
    # The test fixture patched the factory, so use the public class from its module.
    from httpx._client import AsyncClient
    async def cancel(request):
        raise asyncio.CancelledError()
    monkeypatch.setattr(research.httpx, "AsyncClient", lambda **kw: AsyncClient(
        transport=httpx.MockTransport(cancel), **kw))
    with pytest.raises(asyncio.CancelledError):
        run(lambda: research.search_documents("test"))
    assert research_activity.status()["requests"] == {"attempted": 1, "succeeded": 0, "failed": 0, "cancelled": 1}


def test_receipt_from_previous_key_cannot_overwrite_new_status(providers, monkeypatch):
    attempt = research_activity.started("search")
    monkeypatch.setenv("EXA_API_KEY", "changed-fake-key")
    research_activity.finished(attempt, "success", http_status=200, documents=2)
    status = research_activity.status()
    assert status["requests"]["attempted"] == 0
    assert status["last_request"]["status"] == status["last_event"]["status"] == "not_tested"


def test_saved_run_result_does_not_make_new_http_request(providers):
    requests, _ = providers
    store = AsyncMock()
    store.research_result.return_value = {"documents": [], "partial": False}
    async def work():
        current.get().store = store
        await research.search_documents("saved question")
    run(work)
    assert not requests and research_activity.status()["last_event"]["source"] == "saved_run"
    store.reserve_research.assert_not_awaited()


@pytest.mark.parametrize("failure", [False, True])
def test_research_receipts_reach_real_sse(providers, monkeypatch, failure):
    from continuity.api import app as app_module
    from continuity.api.runs import RunManager
    from continuity.graph import sourcing
    from continuity.graph.build import build
    from tests.test_api import stream
    requests, response = providers
    if failure:
        response["http_status"] = 429
    # Exercise the real background graph pump and its shared observer, without
    # buying provider requests or inventing a user session on the hosted app.
    async def find(query, **kwargs):
        await research.search_documents("one shared question")
        return []
    monkeypatch.setattr(sourcing, "find", find)
    monkeypatch.setattr(app_module.app.state, "graph", build())
    monkeypatch.setattr(app_module.app.state, "store", None)
    monkeypatch.setattr(app_module.app.state, "runs", RunManager())
    frames = asyncio.run(stream("/design", {"prompt": "USB motion monitor"}))
    receipts = [f for f in frames if "research" in f]
    assert len(requests) == 1 and receipts
    statuses = [f["research"]["status"] for f in receipts]
    assert ("error" if failure else "success") in statuses
    assert all(f["type"] == "reasoning" for f in receipts)
    assert all("ai" not in f for f in receipts)
    sequence = [f["seq"] for f in frames]
    assert sequence == sorted(set(sequence))
    assert "fake-exa-secret" not in json.dumps(frames)
