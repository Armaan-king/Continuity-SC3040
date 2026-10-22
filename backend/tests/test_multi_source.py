"""Normal sourcing crosses supplier boundaries; all HTTP is simulated."""
import asyncio
import json
import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from continuity import ai_activity, llm
from continuity.api.events import EventStream
from continuity.designs.context import ExecutionContext, current
from continuity.engine.models import Board, PartSpec, Requirements, Slot
from continuity.graph import nodes, sourcing
from continuity.parts import alternatives, evidence, normalize, research, research_activity
from continuity.parts.search import Candidate

REAL_FIND, REAL_CHOOSE = sourcing.find, sourcing.choose
MPN = "TEST-MCU-42"
URL = "https://www.nxp.com/products/test-mcu-42"
QUOTE = f"{MPN} is a low-power microcontroller."
CAPABILITY = f"{MPN} supports IIC and SPI interfaces."


def catalogue(mpn="CAT-MCU"):
    return Candidate("C123", mpn, "NXP", "Microcontroller", None, "", "", 100, 1.0, None)


def web_response():
    return {"results": [{"url": URL, "title": MPN, "text": QUOTE + "\n" + CAPABILITY}],
            "output": {"content": {"candidates": [{"mpn": MPN, "manufacturer": "NXP",
                       "url": URL, "quote": QUOTE, "role": "master"}]}}, "costDollars": {"total": 0.009}}


@pytest.fixture
def providers(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "fake-exa-secret")
    monkeypatch.setenv("CONTINUITY_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("CONTINUITY_FIXTURES", "0")
    monkeypatch.setenv("CONTINUITY_RESEARCH_MAX_CALLS", "6")
    monkeypatch.setenv("CONTINUITY_RESEARCH_BUDGET_USD", "0.12")
    monkeypatch.setattr(research_activity, "_identity", None)
    requests, response = [], web_response()
    client_type = httpx.AsyncClient
    async def handle(request):
        requests.append(request)
        await asyncio.sleep(0)
        return httpx.Response(response.get("http_status", 200), json=response)
    def client(**kw):
        kw.setdefault("transport", httpx.MockTransport(handle))
        return client_type(**kw)
    monkeypatch.setattr(research.httpx, "AsyncClient", client)
    monkeypatch.setattr(sourcing, "find", REAL_FIND)
    monkeypatch.setattr(sourcing, "choose", REAL_CHOOSE)
    monkeypatch.setattr(sourcing, "search", AsyncMock(return_value=[catalogue(f"CAT-{i}") for i in range(6)]))
    monkeypatch.setattr(sourcing.interpret, "unfit_candidates", AsyncMock(return_value=set()))
    return requests, response


def run(work):
    async def go():
        token = current.set(ExecutionContext("multi-source-test"))
        try:
            return await work()
        finally:
            current.reset(token)
    return asyncio.run(go())


def test_full_catalogue_still_searches_web_without_supplier_lookup(providers):
    requests, _ = providers
    found = run(lambda: sourcing.find("low power MCU", purpose="Controller", board="light monitor"))
    assert len(found) == 6 and found[0].mpn == MPN and not found[0].lcsc
    assert found[0].product_url == URL and found[0].stock is None and found[0].unit_price is None
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["type"] == "auto" and payload["numResults"] == 4
    assert payload["contents"] == {"text": {"maxCharacters": 24000}}
    assert "includeDomains" not in payload and "category" not in payload
    assert "outputSchema" in payload and "brief" in payload["systemPrompt"]
    assert sourcing.search.await_count == 1


@pytest.mark.parametrize("which", ["catalogue", "web"])
def test_one_source_failure_keeps_other_candidates(providers, monkeypatch, which):
    _, response = providers
    if which == "catalogue":
        monkeypatch.setattr(sourcing, "search", AsyncMock(side_effect=RuntimeError("unavailable")))
    else:
        response["http_status"] = 429
    found = run(lambda: sourcing.find("controller"))
    assert found and all(c.source == ("exa" if which == "catalogue" else "jlcpcb") for c in found)


def test_duplicate_identity_keeps_inventory_and_independent_evidence(providers):
    web = run(lambda: alternatives.search_candidates("mcu", "controller", "", {}))[0]
    matched = replace(catalogue(MPN), manufacturer="nxp")
    other = replace(web, mpn="WEB-OTHER")
    merged = sourcing.mixed_candidates([matched, catalogue()], [other, web])
    duplicate = [c for c in merged if c.mpn == MPN]
    assert len(duplicate) == 1 and duplicate[0].source == "jlcpcb+exa"
    assert duplicate[0].stock == 100 and duplicate[0].web["source"] == URL


@pytest.mark.parametrize("change", ["invented_quote", "unknown_url", "wrong_variant", "wrong_named_part", "malformed_rows"])
def test_unsubstantiated_discovery_is_not_a_candidate(providers, change):
    _, response = providers
    row = response["output"]["content"]["candidates"][0]
    constraint = {}
    if change == "invented_quote":
        row["quote"] = f"{MPN} has 999 GPIO and costs nothing."
    elif change == "unknown_url":
        row["url"] = "https://invented.example/product"
    elif change == "wrong_variant":
        row["mpn"] = "TEST-MCU-4"
    elif change == "wrong_named_part":
        constraint = {"mpn": "ANOTHER-MODEL"}
    else:
        response["output"]["content"]["candidates"] = "invalid"
    assert run(lambda: alternatives.search_candidates("mcu", "controller", "", constraint)) == []


def test_provider_wrapped_quote_without_mpn_uses_page_identity_only(providers, monkeypatch):
    # Live Exa returned a quoted feature bullet; the exact MPN was in its page
    # heading, not inside that bullet. This is a lead, not an electrical assertion.
    _, response = providers
    response["results"][0]["text"] = MPN + "\nI²C Slave Peripheral"
    row = response["output"]["content"]["candidates"][0]
    row["quote"] = "“I²C Slave Peripheral”"
    row["role"] = None
    async def work():
        found = await sourcing.find("mcu")
        candidate = next(c for c in found if c.web)
        assert candidate.web["summary"] == "I²C Slave Peripheral"
        part = await sourcing.choose(candidate)
        assert part.interfaces == () and part.role is None
    run(work)


def test_web_part_is_admitted_from_source_without_jlc_or_repeat_extraction(providers, monkeypatch):
    requests, _ = providers
    monkeypatch.setattr(llm, "available", lambda: True)
    completion = AsyncMock(return_value={"claims": [{"document": 0, "subject": MPN,
        "identity_quote": MPN, "group": "interfaces", "parameter": "Interface", "value": "IIC",
        "quote": CAPABILITY, "conditions": ""}]})
    monkeypatch.setattr(llm, "complete_json", completion)
    supplier = AsyncMock(side_effect=AssertionError("External products must not require JLCPCB"))
    monkeypatch.setattr(normalize, "normalize", supplier)
    async def work():
        found = await sourcing.find("mcu")
        part = await sourcing.choose(found[0])
        assert part.interfaces == ("I2C",) and part.role == "master"
        assert part.stock is None and part.unit_price is None and part.vmin is None
        assert part.fact_evidence["interfaces"]["source"] == URL
        assert await research.enrich(part) == part
    run(work)
    assert len(requests) == 1 and completion.await_count == 1 and supplier.await_count == 0


def test_missing_full_text_keeps_values_unknown_under_budget(providers, monkeypatch):
    _, response = providers
    response["results"][0]["highlights"] = [response["results"][0].pop("text")]
    monkeypatch.setenv("CONTINUITY_RESEARCH_MAX_CALLS", "1")
    async def work():
        candidate = (await sourcing.find("mcu"))[0]
        part = await sourcing.choose(candidate)
        assert part.vmax is None and part.interfaces == () and part.role is None
        assert part.research[0]["extraction_status"] == "full_source_unavailable"
    run(work)


def test_pinned_function_compares_web_and_catalogue_and_can_choose_web(providers, monkeypatch):
    emitted, compared = [], []
    monkeypatch.setattr(nodes, "_emit", emitted.append)
    async def choose(candidate):
        compared.append(candidate.source)
        return PartSpec(candidate.mpn, candidate.manufacturer, "", "mcu", role="master",
                        interfaces=("I2C",) if candidate.web else ("SPI",), stock=100,
                        temp_min=-40, temp_max=85)
    monkeypatch.setattr(sourcing, "choose", choose)
    monkeypatch.setattr(research, "enrich", AsyncMock(side_effect=lambda p: p))
    sensor = PartSpec("SENSOR", "NXP", "", "sensor", role="peripheral", interfaces=("I2C",))
    state = {"pending": ["mcu"], "requirements": Requirements(), "rails": {},
             "slots": {"mcu": Slot("mcu", "Controller", "core", pinned=True),
                       "sensor": Slot("sensor", "Sensor", "peripherals", part=sensor)},
             "plan": SimpleNamespace(queries={"mcu": "mcu"}), "prompt": "light monitor"}
    result = run(lambda: nodes.select(state, {"configurable": {"events": EventStream("test")}}))
    assert result["slots"]["mcu"].part.mpn == MPN and result["slots"]["mcu"].pinned
    assert len(compared) <= 3 and set(compared) == {"exa", "jlcpcb"}
    text = " ".join(e.get("text", "") for e in emitted)
    assert "independent web sources" in text and "web-discovered" in text and "selected " + MPN in text


def test_parallel_identical_queries_share_paid_response(providers):
    requests, _ = providers
    async def work():
        results = await asyncio.gather(*(sourcing.find("identical mcu") for _ in range(4)))
        assert all(result[0].mpn == MPN for result in results)
        assert current.get().research_calls == 1
    run(work)
    assert len(requests) == 1 and research_activity.status()["cache_hits"] == 3


def test_parallel_prefetch_respects_shared_allowance(providers, monkeypatch):
    requests, _ = providers
    monkeypatch.setenv("CONTINUITY_RESEARCH_MAX_CALLS", "2")
    async def work():
        results = await asyncio.gather(*(sourcing.find(f"mcu {i}") for i in range(8)))
        assert all(results)  # catalogue remains usable for slots beyond the allowance
        assert sum(any(c.web for c in result) for result in results) == 2
    run(work)
    assert len(requests) == 2 and research_activity.status()["skipped"] == 6


def test_web_candidates_survive_checkpoint_and_recovery_offer(providers):
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from continuity.graph.recovery import catalogue_offer
    candidate = run(lambda: sourcing.find("controller"))[0]
    serializer = JsonPlusSerializer(allowed_msgpack_modules=[("continuity.parts.search", "Candidate")])
    restored = serializer.loads_typed(serializer.dumps_typed(candidate))
    assert restored == candidate
    offer = catalogue_offer(restored, 1)
    assert offer["source"] == URL and "Web candidate" in offer["verification"]
    alternative = sourcing.alternatives([restored], restored, "trial")[0]
    assert alternative.stock is None and alternative.lead_time_days is None


@pytest.mark.skipif(not os.environ.get("CONTINUITY_TEST_DB"), reason="requires disposable PostgreSQL")
def test_web_discovery_cache_and_allowance_survive_execution_restart(providers, monkeypatch):
    from tests.test_projects import a_store, signed_in
    from tests.test_revisions import seed
    requests, _ = providers
    monkeypatch.setenv("CONTINUITY_RESEARCH_MAX_CALLS", "1")
    async def go():
        async with a_store() as store, signed_in() as http:
            await seed(store, http)
            for execution in range(2):
                token = current.set(ExecutionContext("revision-reference", store))
                try:
                    found = await sourcing.find("same controller")
                    assert any(c.web for c in found)
                    if execution:
                        assert not any(c.web for c in await sourcing.find("different controller"))
                finally:
                    current.reset(token)
    asyncio.run(go())
    assert len(requests) == 1
    assert research_activity.status()["cache_hits"] == 1
