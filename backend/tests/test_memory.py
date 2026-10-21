"""Continuity findings and the user-facing project/part memory."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import httpx
import pytest

from continuity.api.app import _replay, _run, _run_bom, app
from continuity.api.events import EventStream
from continuity.api.memory import Finding, FindingRecorder
from continuity.api.store import Store
from continuity.engine.models import PartSpec


def test_a_repair_records_the_original_and_replacement_part():
    recorder = FindingRecorder()
    recorder.feed({"type": "selection", "slot": "regulator", "part": {"mpn": "BAD-LDO"}})
    recorder.feed(
        {
            "type": "conflict",
            "rule": "thermal_dissipation",
            "involved": ["regulator"],
            "message": "Too hot.",
        }
    )
    recorder.feed({"type": "repair", "slot": "regulator", "action": "change_topology"})
    recorder.feed({"type": "selection", "slot": "regulator", "part": {"mpn": "GOOD-BUCK"}})
    recorder.feed({"type": "done"})

    assert recorder.findings() == [
        Finding(
            rule="thermal_dissipation",
            slot="regulator",
            mpn="BAD-LDO",
            verdict="Too hot.",
            outcome="repaired",
            action="change_topology",
            replacement_mpn="GOOD-BUCK",
            worked=True,
        )
    ]


def test_a_passing_check_marks_a_repaired_finding_as_a_worked_precedent():
    recorder = FindingRecorder()
    recorder.feed({"type": "selection", "slot": "regulator", "part": {"mpn": "BAD-LDO"}})
    recorder.feed(
        {
            "type": "conflict",
            "rule": "thermal_dissipation",
            "involved": ["regulator"],
            "message": "Too hot.",
            "signature": "thermal_dissipation|regulator|linear|pkg:SOT|drop:>=8V|load:100-500mA",
        }
    )
    recorder.feed({"type": "repair", "slot": "regulator", "action": "change_topology"})
    recorder.feed({"type": "selection", "slot": "regulator", "part": {"mpn": "GOOD-BUCK"}})
    recorder.feed(
        {
            "type": "check",
            "slot": "regulator",
            "rule": "thermal_dissipation",
            "status": "pass",
        }
    )

    finding = recorder.findings()[0]
    assert finding.signature == (
        "thermal_dissipation|regulator|linear|pkg:SOT|drop:>=8V|load:100-500mA"
    )
    assert finding.worked is True


def test_a_conflict_uses_the_mpn_selected_for_its_slot():
    recorder = FindingRecorder()
    recorder.feed({"type": "selection", "slot": "sensor", "part": {"mpn": "SHT40"}})
    recorder.feed(
        {
            "type": "conflict",
            "rule": "availability",
            "involved": ["sensor"],
            "message": "No stock.",
        }
    )
    recorder.feed({"type": "done"})

    assert recorder.findings()[0].mpn == "SHT40"


def test_a_conflict_without_an_earlier_selection_is_not_persisted_without_an_mpn():
    recorder = FindingRecorder()
    recorder.feed(
        {"type": "conflict", "rule": "availability", "involved": ["sensor"], "message": "No stock."}
    )
    recorder.feed({"type": "done"})

    assert recorder.findings() == []


def test_open_findings_are_unresolved_when_the_run_ends():
    recorder = FindingRecorder()
    recorder.feed({"type": "selection", "slot": "sensor", "part": {"mpn": "SHT40"}})
    recorder.feed(
        {"type": "conflict", "rule": "availability", "involved": ["sensor"], "message": "No stock."}
    )
    recorder.feed({"type": "done"})

    assert recorder.findings()[0].outcome == "unresolved"


def test_existing_acceptance_reasoning_marks_the_open_finding_accepted():
    recorder = FindingRecorder()
    recorder.feed({"type": "selection", "slot": "sensor", "part": {"mpn": "SHT40"}})
    recorder.feed(
        {"type": "conflict", "rule": "availability", "involved": ["sensor"], "message": "No stock."}
    )
    recorder.feed(
        {
            "type": "reasoning",
            "text": "Accepted on your say-so — availability on Sensor stays on the board as a warning.",
        }
    )
    recorder.feed({"type": "done"})

    assert recorder.findings()[0].outcome == "accepted"


class _ConflictGraph:
    async def astream(self, _payload, config, stream_mode):
        stream = config["configurable"]["events"]
        yield "custom", {"type": "selection", "slot": "sensor", "part": {"mpn": "SHT40"}}
        yield "custom", {"type": "conflict", "rule": "availability", "involved": ["sensor"], "message": "No stock."}
        yield "custom", stream.done(slots=1, conflicts_resolved=0, elapsed_s=0)


def test_a_run_without_a_store_finishes_without_persisting_findings():
    async def go():
        thread_id = "no-store-findings"
        from continuity.api import app as app_module

        app_module.FINDING_RECORDERS.clear()
        app_module.STREAMS[thread_id] = EventStream(thread_id)
        frames = [item async for item in _run(_ConflictGraph(), thread_id, {"prompt": "brief"})]
        return frames, app_module.FINDING_RECORDERS

    frames, recorders = asyncio.run(go())
    assert frames
    assert recorders == {}


class _RecordingStore:
    def __init__(self) -> None:
        self.findings: list[Finding] | None = None
        self.facts: list[tuple[str, str, str, str | None]] = []

    async def save_bom(self, _thread_id: str, _rows: list[dict]) -> None:
        pass

    async def save_summary(self, _thread_id: str, _summary: dict) -> None:
        pass

    async def save_progress(self, _thread_id: str, _last_seq: int, _status: str) -> None:
        pass

    async def save_findings(self, _thread_id: str, findings: list[Finding]) -> None:
        self.findings = findings

    async def part_facts(self, _mpns: list[str]) -> dict[str, list[dict]]:
        return {}

    async def save_part_facts(self, facts) -> None:
        self.facts.extend(facts)


def test_the_design_stream_persists_its_recorded_findings():
    async def go():
        from continuity.api import app as app_module

        thread_id = "design-findings"
        store = _RecordingStore()
        app_module.STREAMS[thread_id] = EventStream(thread_id)
        app_module.FINDING_RECORDERS.clear()
        assert [item async for item in _run(_ConflictGraph(), thread_id, {"prompt": "brief"}, store)]
        return store.findings

    findings = asyncio.run(go())
    assert findings is not None
    assert findings[0].mpn == "SHT40"


def test_the_walkthrough_replay_persists_its_recorded_findings(monkeypatch):
    frames = [
        {"type": "selection", "slot": "sensor", "part": {"mpn": "SHT40"}},
        {"type": "conflict", "rule": "availability", "involved": ["sensor"], "message": "No stock."},
        {"type": "bom", "rows": []},
        {"type": "done", "summary": {"slots": 1, "placed": 1, "conflicts_resolved": 0, "elapsed_s": 0}},
    ]
    monkeypatch.setattr("continuity.api.app.walkthrough_frames", lambda: frames)

    async def go():
        store = _RecordingStore()
        assert [item async for item in _replay("walkthrough-findings", store)]
        return store.findings

    findings = asyncio.run(go())
    assert findings is not None
    assert findings[0].mpn == "SHT40"


def test_the_bom_validation_stream_persists_its_recorded_findings(monkeypatch):
    async def events_in(_rows, _stream, _brief):
        yield {"type": "selection", "slot": "sensor", "part": {"mpn": "SHT40"}}
        yield {"type": "conflict", "rule": "availability", "involved": ["sensor"], "message": "No stock."}
        yield {"type": "bom", "rows": []}
        yield {"type": "done", "summary": {"slots": 1, "placed": 1, "conflicts_resolved": 0, "elapsed_s": 0}}

    monkeypatch.setattr("continuity.api.app.bom.validate_bom", events_in)

    async def go():
        store = _RecordingStore()
        stream = EventStream("bom-findings")
        assert [item async for item in _run_bom([], None, stream, store)]
        return store.findings

    findings = asyncio.run(go())
    assert findings is not None
    assert findings[0].mpn == "SHT40"


def test_the_bom_validation_stream_persists_its_placed_part_facts(monkeypatch):
    async def events_in(_rows, _stream, _brief):
        from continuity.api import bom as bom_module

        observer = bom_module._placed_parts_observer.get()
        assert observer is not None
        observer(
            [
                PartSpec(
                    mpn="TPS54331DR",
                    manufacturer="TI",
                    description="buck",
                    category="DC-DC",
                    package="SOIC-8",
                )
            ]
        )
        yield {"type": "bom", "rows": []}
        yield {"type": "done", "summary": {"slots": 1, "placed": 1, "conflicts_resolved": 0, "elapsed_s": 0}}

    monkeypatch.setattr("continuity.api.app.bom.validate_bom", events_in)

    async def go():
        store = _RecordingStore()
        assert [item async for item in _run_bom([], None, EventStream("bom-dossier"), store)]
        return store.facts

    assert asyncio.run(go()) == [("TPS54331DR", "package", "SOIC-8", None)]


DB_URL = os.environ.get("CONTINUITY_TEST_DB")
database = pytest.mark.skipif(not DB_URL, reason="set CONTINUITY_TEST_DB to run memory tests")


@asynccontextmanager
async def a_store():
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(DB_URL, min_size=1, max_size=2, open=False) as pool:
        await pool.open()
        store = Store(pool)
        await store.setup()
        async with pool.connection() as conn:
            await conn.execute("TRUNCATE users, sessions, projects, threads CASCADE")
        previous = app.state.store
        app.state.store = store
        try:
            yield store
        finally:
            app.state.store = previous


async def _signed_in(email: str) -> httpx.AsyncClient:
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    await client.post("/auth/register", json={"email": email, "password": "a-good-password"})
    return client


async def _board(store: Store, user_id: str, project_id: str, thread_id: str, mpn: str) -> None:
    await store.create_thread(thread_id, project_id, user_id, "brief")
    await store.save_bom(
        thread_id,
        [{"slot": "sensor", "mpn": mpn, "manufacturer": "Sensirion", "lifecycle": "active"}],
    )


@database
def test_memory_joins_two_threads_under_one_part_and_keeps_other_users_out():
    async def go():
        async with a_store() as store:
            mine = await _signed_in("mine@example.com")
            theirs = await _signed_in("theirs@example.com")
            me = (await mine.get("/auth/me")).json()
            project = await store.create_project(me["id"], "Weather")
            for thread_id in ("thread-one", "thread-two"):
                await _board(store, me["id"], project.id, thread_id, "SHT40")
                await store.save_findings(
                    thread_id,
                    [Finding("availability", "sensor", "SHT40", "No stock.", "unresolved")],
                )
            return (await mine.get("/memory")).json(), await theirs.get("/memory")

    memory, other = asyncio.run(go())
    assert memory["parts"][0]["mpn"] == "SHT40"
    assert len(memory["parts"][0]["findings"]) == 2
    assert other.json() == {"projects": [], "parts": [], "parts_capped": False, "part_limit": 100}


@database
def test_memory_reports_a_part_used_in_three_projects():
    async def go():
        async with a_store() as store:
            mine = await _signed_in("mine@example.com")
            me = (await mine.get("/auth/me")).json()
            for number in range(3):
                project = await store.create_project(me["id"], f"P{number}")
                await _board(store, me["id"], project.id, f"thread-{number}", "COMMON")
            return (await mine.get("/memory")).json()

    memory = asyncio.run(go())
    assert len(memory["parts"][0]["used_in"]) == 3


def test_the_acceptance_pattern_matches_the_message_the_run_actually_emits():
    """The two sides of an unstructured signal, pinned together.

    Accepting an escalation has no structured signal — `api/memory.py` reads the narration
    line to tell `accepted` from `unresolved`. Building the message through the real
    emitter here means a rewording breaks this test rather than silently recording every
    waived finding as unresolved, which nothing else would catch.
    """
    from continuity.api.memory import _ACCEPTED
    from continuity.graph.nodes import acceptance_message

    message = acceptance_message("temperature_rating", "WiFi BLE MCU")
    match = _ACCEPTED.match(message)

    assert match is not None, f"the recorder can no longer read {message!r}"
    assert match.group("rule") == "temperature rating"


def test_a_conflicts_first_involved_slot_is_its_subject():
    """The recorder reads `involved[0]` as the slot at fault. This is why that is safe.

    `events.conflict` warns in its own docstring that *"involved is everyone
    participating, not everyone at fault"* — good advice, and it would make the recorder
    look wrong. What makes it right is `Verdict.__post_init__`, which rebuilds `involved`
    as `dict.fromkeys((subject, *involved))`, putting the subject first and deduplicating.

    That ordering is documented there for a different reason — a rail whose only consumer
    is its subject used to render twice — so nothing currently protects the recorder from
    it being changed. This test does.
    """
    from continuity.api import events
    from continuity.engine.models import Verdict

    verdict = Verdict(
        rule="thermal_dissipation",
        status="fail",
        detail="…",
        subject="regulator",
        involved=("mcu", "regulator", "oled"),
    )
    frame = events.EventStream("t").conflict(verdict)

    assert verdict.involved[0] == verdict.subject
    assert frame["involved"][0] == "regulator"
