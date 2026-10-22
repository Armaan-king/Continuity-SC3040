"""Native Exa contracts, source identity, budgets, and conservative trial facts."""
import asyncio
import copy
import json

import httpx
import pytest

from continuity.designs.context import ExecutionContext, current
from continuity.engine.models import Slot
from continuity.parts import alternatives, research

URL = "https://www.st.com/en/microcontrollers-microprocessors/stm32g030k6.html"
QUOTE = "STM32G030K6 is a general-purpose microcontroller with a 32-bit Arm Cortex-M0+ CPU."
ROW = {"mpn": "STM32G030K6", "manufacturer": "STMicroelectronics", "url": URL, "quote": QUOTE, "role": "master"}


@pytest.fixture
def transport(monkeypatch):
    requests = []
    response = {"rows": [copy.deepcopy(ROW)], "text": QUOTE + " Operating conditions: 2.0 to 3.6 V.", "failed": False}
    def handle(request):
        requests.append(request)
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": [{"url": URL, "highlights": [QUOTE]}],
                "output": {"content": {"candidates": response["rows"]}}})
        assert request.url.path == "/contents"
        return httpx.Response(200, json={"results": [{"url": URL, "text": response["text"]}],
            "statuses": [{"id": URL, "status": "error" if response["failed"] else "success"}]})
    client = httpx.AsyncClient
    monkeypatch.setenv("CONTINUITY_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("CONTINUITY_FIXTURES", "0")
    monkeypatch.setenv("EXA_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr(research.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    return requests, response


def run(exclude=()):
    async def go():
        token = current.set(ExecutionContext("alternative-test"))
        try:
            return await alternatives.investigate(Slot("mcu", "I2C microcontroller", "core", constraint={"category": "mcu"}),
                                                   "plant light monitor", "no I2C controller", exclude=exclude)
        finally:
            current.reset(token)
    return asyncio.run(go())


def test_search_then_contents_offer_external_part_without_inventing_specs(transport):
    requests, _ = transport
    offered, message = run()
    assert len(requests) == 2
    search = json.loads(requests[0].content)
    assert search["type"] == "auto" and search["contents"] == {"highlights": True}
    assert search["outputSchema"] == alternatives.SCHEMA
    assert "includeDomains" not in search and "numResults" not in search
    contents = json.loads(requests[1].content)
    assert contents["urls"] == [URL] and "text" in contents and "contents" not in contents
    assert len(offered) == 1 and offered[0]["source"] == URL
    assert "beyond JLCPCB" in message
    part = asyncio.run(alternatives.materialize(offered[0]))
    assert part.distributor == "External source"
    assert part.stock is None and part.unit_price is None
    assert part.vmin is None and part.vmax is None and part.interfaces == ()
    assert part.role == "master"  # Exact model and controller term on recognised manufacturer.
    assert part.research[0]["extraction_status"] == "ai_unavailable"


@pytest.mark.parametrize("mutation", ["fake_quote", "wrong_model", "unreturned_url", "failed_contents", "bad_shape", "bad_url_type"])
def test_unverifiable_or_malformed_results_never_become_choices(transport, mutation):
    requests, response = transport
    if mutation == "fake_quote":
        response["rows"][0]["quote"] = "STM32G030K6 is a fictional quotation that was not retrieved."
    elif mutation == "wrong_model":
        response["text"] = "STM32F103 is another microcontroller entirely."
    elif mutation == "unreturned_url":
        response["rows"][0]["url"] = "https://www.st.com/unreturned"
    elif mutation == "failed_contents":
        response["failed"] = True
    elif mutation == "bad_shape":
        response["rows"] = {"broken": "not an array"}
    elif mutation == "bad_url_type":
        response["rows"][0]["url"] = []
    offered, message = run()
    assert offered == []
    assert message and len(requests) <= 2


def test_previous_component_is_excluded_before_buying_contents(transport):
    requests, _ = transport
    offered, _ = run(exclude=[ROW["mpn"]])
    assert offered == [] and len(requests) == 1


def test_budget_exhaustion_does_not_offer_unread_search_snippet(transport, monkeypatch):
    requests, _ = transport
    monkeypatch.setenv("CONTINUITY_RESEARCH_MAX_CALLS", "1")
    offered, message = run()
    assert offered == [] and "budget_exhausted" in message
    assert len(requests) == 1


def test_untrusted_site_cannot_assign_controller_role():
    part = asyncio.run(alternatives.materialize({"mpn": ROW["mpn"], "manufacturer": ROW["manufacturer"],
        "role": "master", "category": "mcu", "summary": QUOTE, "source": "https://example.org/catalogue",
        "documents": []}))
    assert part.role is None and part.interfaces == ()


@pytest.mark.parametrize("quote", ["STM32G030K6 is not a microcontroller.",
                                    "STM32G030K6 communicates with an external microcontroller."])
def test_role_requires_a_direct_positive_description_of_this_product(quote):
    part = asyncio.run(alternatives.materialize({"mpn": ROW["mpn"], "manufacturer": ROW["manufacturer"],
        "role": "master", "category": "mcu", "summary": quote, "source": URL, "documents": []}))
    assert part.role is None
