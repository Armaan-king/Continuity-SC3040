"""Progress narration around the calls that place and repair parts."""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

from continuity.api.events import EventStream
from continuity.engine.models import Requirements
from continuity.graph import nodes, sourcing
from continuity.parts.search import Candidate
from tests import parts
from tests.boards import slot


def _candidate(mpn: str) -> Candidate:
    return Candidate(
        lcsc=f"C{mpn}",
        mpn=mpn,
        manufacturer="Test manufacturer",
        description="Test regulator",
        package="SOT-23-5",
        category="LDO Regulator",
        subcategory="LDO Regulator",
        stock=100,
        unit_price=0.1,
        library_type=None,
    )


def _config(thread_id: str) -> dict:
    return {"configurable": {"events": EventStream(thread_id)}}


def test_select_narrates_search_count_and_read_before_emitting_a_candidate(monkeypatch):
    """All progress events arrive before the slow normalisation produces a candidate."""
    emitted: list[dict] = []
    candidates = [_candidate("PART-A"), _candidate("PART-B"), _candidate("PART-C")]
    search_reasoning_counts: list[int] = []
    normalisation_reasoning_counts: list[int] = []

    async def find(query, *, constraint=None, **_context):
        search_reasoning_counts.append(len([e for e in emitted if e["type"] == "reasoning"]))
        return candidates

    async def choose(candidate):
        normalisation_reasoning_counts.append(
            len([e for e in emitted if e["type"] == "reasoning"])
        )
        return parts.ldo_600ma(mpn=candidate.mpn)

    monkeypatch.setattr(nodes, "_emit", emitted.append)
    monkeypatch.setattr(sourcing, "find", find)
    monkeypatch.setattr(sourcing, "choose", choose)
    state = {
        "pending": ["regulator"],
        "slots": {"regulator": slot("regulator")},
        "plan": SimpleNamespace(queries={"regulator": "3.3V regulator"}),
    }

    asyncio.run(nodes.select(state, _config("select-narration")))

    assert search_reasoning_counts == [1]
    assert normalisation_reasoning_counts == [3]
    assert [event["type"] for event in emitted] == [
        "reasoning",
        "reasoning",
        "reasoning",
        "candidate",
    ]
    assert [event["text"] for event in emitted if event["type"] == "reasoning"] == [
        "Searching JLCPCB for “3.3V regulator”.",
        "3 viable candidates.",
        "Reading specs, lifecycle, and datasheet for PART-A.",
    ]


def test_select_that_finds_nothing_only_narrates_work_that_started(monkeypatch):
    emitted: list[dict] = []
    search_reasoning_counts: list[int] = []

    async def find(query, *, constraint=None, **_context):
        search_reasoning_counts.append(len([e for e in emitted if e["type"] == "reasoning"]))
        return []

    async def choose(candidate):
        raise AssertionError("an empty search must not start normalisation")

    monkeypatch.setattr(nodes, "_emit", emitted.append)
    monkeypatch.setattr(sourcing, "find", find)
    monkeypatch.setattr(sourcing, "choose", choose)
    state = {
        "pending": ["regulator"],
        "slots": {"regulator": slot("regulator")},
        "plan": SimpleNamespace(queries={"regulator": "3.3V regulator"}),
    }

    asyncio.run(nodes.select(state, _config("empty-narration")))

    assert search_reasoning_counts == [1]
    assert [event["type"] for event in emitted] == [
        "reasoning",
        "reasoning",
        "reasoning",
        "check",
    ]


def test_apply_research_narrates_search_count_and_read_before_candidate(monkeypatch):
    emitted: list[dict] = []
    candidates = [_candidate("PART-A"), _candidate("PART-B"), _candidate("PART-C")]
    normalisation_reasoning_counts: list[int] = []

    async def find(query, *, constraint=None, **_context):
        return candidates

    async def choose(candidate):
        normalisation_reasoning_counts.append(
            len([e for e in emitted if e["type"] == "reasoning"])
        )
        return parts.buck_3v3(mpn=candidate.mpn)

    monkeypatch.setattr(nodes, "_emit", emitted.append)
    monkeypatch.setattr(sourcing, "find", find)
    monkeypatch.setattr(sourcing, "choose", choose)
    state = {
        "requirements": Requirements(),
        "current": "regulator",
        "slots": {"regulator": slot("regulator", parts.ldo_600ma())},
        "rails": {},
        "constraint": {"topology": "buck"},
        "candidates": {},
        "cursor": {},
        "plan": SimpleNamespace(queries={"regulator": "3.3V regulator"}),
        "conflicts_resolved": 0,
    }

    asyncio.run(nodes.apply(state, _config("apply-narration")))

    assert normalisation_reasoning_counts == [3]
    assert [event["type"] for event in emitted] == [
        "reasoning",
        "reasoning",
        "reasoning",
        "candidate",
    ]
    assert [event["text"] for event in emitted if event["type"] == "reasoning"] == [
        "Re-searching with topology=buck.",
        "3 viable candidates.",
        "Reading specs, lifecycle, and datasheet for PART-A.",
    ]


def test_narration_never_adjudicates(monkeypatch):
    emitted: list[dict] = []

    monkeypatch.setattr(nodes, "_emit", emitted.append)
    monkeypatch.setattr(sourcing, "find", lambda *args, **kwargs: _immediate([]))
    state = {
        "pending": ["regulator"],
        "slots": {"regulator": slot("regulator")},
        "plan": SimpleNamespace(queries={"regulator": "3.3V regulator"}),
    }

    asyncio.run(nodes.select(state, _config("no-verdicts")))

    assert not [
        event
        for event in emitted
        if event["type"] == "reasoning" and re.search(r"\b(pass|fail|conflict)\b", event["text"], re.I)
    ]


async def _immediate(value):
    return value
