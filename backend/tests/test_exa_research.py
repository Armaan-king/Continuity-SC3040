import asyncio
import httpx
import pytest
from fuze.parts import research
from fuze.designs.context import ExecutionContext, current


def run(work):
    async def go():
        token = current.set(ExecutionContext("research-test"))
        try:
            return await work()
        finally:
            current.reset(token)
    return asyncio.run(go())


@pytest.fixture
def transport(monkeypatch):
    requests = []
    reply = {"results": [{"url": "https://manufacturer.example/part", "title": "Part", "highlights": ["Operating Voltage: 1.8V~3.6V"]}], "costDollars": {"total": 0.007}}
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=reply)
    client = httpx.AsyncClient
    monkeypatch.setenv("FUZE_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("FUZE_FIXTURES", "0")
    monkeypatch.setenv("EXA_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr(research.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    return requests, reply


def test_native_search_contract_and_deduplication(transport):
    import json
    requests, _ = transport
    async def work():
        first = await research.search_documents("manufacturer sensor datasheet")
        second = await research.search_documents("manufacturer sensor datasheet")
        assert first == second
        assert first["documents"][0]["verification"] == "unverified"
    run(work)
    assert len(requests) == 1
    assert json.loads(requests[0].content) == {"query": "manufacturer sensor datasheet", "type": "auto", "contents": {"highlights": True}}
    assert requests[0].headers["x-api-key"] == "test-key-not-a-secret"


def test_contents_checks_per_url_failures_and_request_shape(transport):
    import json
    requests, reply = transport
    reply["statuses"] = [{"id": "https://manufacturer.example/part", "status": "error"}]
    result = run(lambda: research.read_documents(["https://manufacturer.example/part"]))
    assert result["documents"] == []
    assert result["partial"] is True
    payload = json.loads(requests[0].content)
    assert "contents" not in payload
    assert "text" in payload and "highlights" not in payload


def test_budget_stops_new_calls(transport, monkeypatch):
    requests, _ = transport
    monkeypatch.setenv("FUZE_RESEARCH_MAX_CALLS", "1")
    async def work():
        await research.search_documents("first")
        return await research.search_documents("second")
    assert run(work)["error"] == "budget_exhausted"
    assert len(requests) == 1


@pytest.mark.parametrize("url", ["http://example.com", "https://127.0.0.1", "https://localhost", "https://169.254.169.254/latest", "https://user:pass@example.com", "file:///etc/passwd", "https://example.com:8080"])
def test_only_public_https_document_urls_are_accepted(url):
    assert research.public_url(url) is None


def test_missing_key_never_makes_a_request(transport, monkeypatch):
    requests, _ = transport
    monkeypatch.delenv("EXA_API_KEY")
    assert run(lambda: research.search_documents("sensor"))["error"] == "disabled"
    assert not requests
