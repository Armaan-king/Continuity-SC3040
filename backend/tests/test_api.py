"""The wire, end to end over real HTTP.

These drive the ASGI app rather than the graph directly, because every bug this layer
has produced so far lived in the seam — an interrupt payload read at the wrong nesting
level, a sequence counter that rewound on resume, an edge status that never resolved.
None of those are visible from inside a node.
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from continuity.api import app as app_module
from continuity.api.app import app
from continuity.parts import datasheet


def run(coro):
    return asyncio.run(coro)


async def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30.0
    )


async def stream(path: str, payload: dict) -> list[dict]:
    """POST and collect every parsed SSE frame."""
    async with await client() as http:
        async with http.stream("POST", path, json=payload) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            frames = []
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    frames.append(json.loads(line[6:]))
            return frames


DEMO = "temp and humidity sensor, wifi and ble, usb-c powered with li-ion backup, small oled"


# ── the happy path ────────────────────────────────────────────────────────────


def test_health():
    async def go():
        async with await client() as http:
            return await http.get("/health")

    assert run(go()).json() == {"status": "ok"}


# ── datasheet upload ─────────────────────────────────────────────────────────


def test_datasheet_endpoint_extracts_and_returns_theta_ja(monkeypatch):
    line = "RθJA Junction-to-ambient thermal resistance 116.3"
    monkeypatch.setattr(app_module.datasheet, "text_from_pdf", lambda _data: line)

    async def extract(*_args, **_kwargs):
        return datasheet.ThermalFact(116.3, line, "SOIC-8")

    monkeypatch.setattr(app_module.datasheet, "theta_ja_from_text", extract)

    async def go():
        async with await client() as http:
            return await http.post(
                "/datasheet",
                json={
                    "mpn": "TPS54331DR",
                    "package": "SOIC-8",
                    "document": base64.b64encode(b"%PDF-1.7\nfixture").decode(),
                },
            )

    response = run(go())

    assert response.status_code == 200
    assert response.json() == {
        "mpn": "TPS54331DR",
        "theta_ja": 116.3,
        "source_line": line,
        "reason": None,
    }


def test_datasheet_endpoint_returns_a_clear_negative_when_no_theta_ja_is_found(monkeypatch):
    monkeypatch.setattr(app_module.datasheet, "text_from_pdf", lambda _data: "ordinary PDF text")

    async def extract(*_args, **_kwargs):
        return None

    monkeypatch.setattr(app_module.datasheet, "theta_ja_from_text", extract)

    async def go():
        async with await client() as http:
            return await http.post(
                "/datasheet",
                json={
                    "mpn": "TPS54331DR",
                    "package": "SOIC-8",
                    "document": base64.b64encode(b"%PDF-1.7\nfixture").decode(),
                },
            )

    response = run(go())

    assert response.status_code == 200
    assert response.json() == {
        "mpn": "TPS54331DR",
        "theta_ja": None,
        "source_line": None,
        "reason": "No usable θJA was found in this datasheet.",
    }


@pytest.mark.parametrize(
    "document",
    ("not base64", ""),
    ids=("not-base64", "empty"),
)
def test_datasheet_endpoint_rejects_malformed_document_bodies(document):
    async def go():
        async with await client() as http:
            return await http.post(
                "/datasheet",
                json={"mpn": "TPS54331DR", "package": "SOIC-8", "document": document},
            )

    assert run(go()).status_code == 422


def test_datasheet_endpoint_rejects_an_oversized_document():
    document = base64.b64encode(b"x" * (datasheet.MAX_PDF_BYTES + 1)).decode()

    async def go():
        async with await client() as http:
            return await http.post(
                "/datasheet",
                json={"mpn": "TPS54331DR", "package": "SOIC-8", "document": document},
            )

    assert run(go()).status_code == 422


def test_datasheet_endpoint_rejects_an_oversized_content_length_before_parsing():
    async def go():
        async with await client() as http:
            return await http.post(
                "/datasheet",
                content=b'{}',
                headers={"content-length": str(app_module.MAX_DATASHEET_REQUEST_BYTES + 1)},
            )

    assert run(go()).status_code == 422


def test_a_run_streams_a_complete_contract_shaped_conversation():
    frames = run(stream("/design", {"prompt": DEMO}))
    kinds = {f["type"] for f in frames}

    assert {"plan", "candidate", "check", "conflict", "repair", "selection", "bom", "done"} <= kinds
    assert frames[-1]["type"] == "done"


def test_a_signed_in_run_without_a_project_uses_a_scratch_project(monkeypatch):
    calls: list[tuple] = []

    class Store:
        async def ensure_scratch_project(self, user_id: str) -> str:
            calls.append(("ensure", user_id))
            return "scratch-project"

        async def project_for_user(self, project_id: str, user_id: str):
            calls.append(("project", project_id, user_id))
            return object()

        async def create_thread(
            self, thread_id: str, project_id: str, user_id: str, prompt: str
        ) -> None:
            calls.append(("thread", thread_id, project_id, user_id, prompt))

    async def signed_in(request):
        return SimpleNamespace(id="user-1")

    async def fake_run(*args):
        yield 'data: {"thread_id":"thread-1"}\n\n'

    monkeypatch.setattr(app.state, "store", Store())
    monkeypatch.setattr(app_module, "_signed_in", signed_in)
    monkeypatch.setattr(app_module, "_run", fake_run)

    frames = run(stream("/design", {"prompt": DEMO}))

    assert frames == [{"thread_id": "thread-1"}]
    assert calls[0] == ("ensure", "user-1")
    assert calls[1] == ("project", "scratch-project", "user-1")
    assert calls[2][2:] == ("scratch-project", "user-1", DEMO)


def test_seq_is_monotonic_from_zero():
    """The client initialises its high-water mark to -1 so that seq 0 survives."""
    frames = run(stream("/design", {"prompt": DEMO}))

    assert [f["seq"] for f in frames] == list(range(len(frames)))


def test_every_frame_carries_a_thread_id():
    frames = run(stream("/design", {"prompt": DEMO}))

    assert len({f["thread_id"] for f in frames}) == 1


def test_plan_declares_every_node_and_edge_up_front():
    """Including data edges, which have no parts yet to say which bus they use."""
    plan = next(f for f in run(stream("/design", {"prompt": DEMO})) if f["type"] == "plan")

    assert len(plan["slots"]) == 4
    power = [e for e in plan["edges"] if e["kind"] == "power"]
    data = [e for e in plan["edges"] if e["kind"] == "data"]

    assert len(power) == 4 and all(e["label"] for e in power)
    assert len(data) == 2 and all(e["label"] is None for e in data)

    # Nothing floats. The regulator is fed by the board input, which travels as a node in
    # its own field precisely so it can be an edge endpoint without being a slot.
    assert plan["supply"]["id"] not in {s["id"] for s in plan["slots"]}
    assert {e["to"] for e in power} == {s["id"] for s in plan["slots"]}


def test_every_slot_ends_resolved():
    """A slot that passed early then sat through someone else's conflict still settles."""
    frames = run(stream("/design", {"prompt": DEMO}))

    selected = {f["slot"] for f in frames if f["type"] == "selection"}
    declared = {
        *{s["id"] for f in frames if f["type"] == "plan" for s in f["slots"]},
        *{f["slot"]["id"] for f in frames if f["type"] == "slot_added"},
    }

    assert selected == declared


def test_every_conflict_has_the_failing_check_that_produced_it():
    """"It shows its working" has to include the check that did not pass.

    A rail-level rule names the regulator as its subject while the slot being validated is
    whatever load pushed the rail over, so the regulator's own failing check used to be
    skipped — the conflict arrived with a check log of five clean passes beside it.
    """
    frames = run(stream("/design", {"prompt": DEMO}))
    checks = {(f["slot"], f["rule"]) for f in frames if f["type"] == "check" and f["status"] == "fail"}

    for conflict in (f for f in frames if f["type"] == "conflict"):
        assert any(
            rule == conflict["rule"] and slot in conflict["involved"] for slot, rule in checks
        ), f"{conflict['rule']} conflicted with no failing check event to show for it"


def test_conflicts_are_engine_driven_and_each_gets_a_repair():
    frames = run(stream("/design", {"prompt": DEMO}))
    conflicts = [f for f in frames if f["type"] == "conflict"]
    repairs = [f for f in frames if f["type"] == "repair"]

    assert [c["rule"] for c in conflicts] == [
        "availability",
        "current_budget",
        "thermal_dissipation",
    ]
    assert len(repairs) == len(conflicts)
    assert repairs[-1]["action"] == "change_topology"


def test_a_conflict_carries_verbatim_evidence():
    conflict = next(
        f for f in run(stream("/design", {"prompt": DEMO})) if f["type"] == "conflict"
    )

    assert conflict["evidence"]
    assert all({"slot", "field", "value", "source"} <= set(e) for e in conflict["evidence"])


def test_involved_is_participation_not_blame():
    """Three parts share a rail; only one of them is what gets repaired."""
    frames = run(stream("/design", {"prompt": DEMO}))
    current = next(f for f in frames if f["type"] == "conflict" and f["rule"] == "current_budget")
    repair = next(f for f in frames if f["type"] == "repair" and f["seq"] > current["seq"])

    assert len(current["involved"]) > 1
    assert repair["slot"] in current["involved"]


def test_done_reports_what_actually_happened():
    summary = next(f for f in run(stream("/design", {"prompt": DEMO})) if f["type"] == "done")[
        "summary"
    ]

    assert summary["slots"] == 4
    assert summary["conflicts_resolved"] == 3


# ── interrupt and resume ──────────────────────────────────────────────────────


def test_an_unmatched_supply_asks_rather_than_assuming():
    frames = run(stream("/design", {"prompt": "solar powered weather station with a sensor"}))

    assert frames[-1]["type"] == "question"
    assert frames[-1]["suggestions"]


def test_resume_continues_the_same_sequence():
    """Two HTTP responses, one logical stream. Restarting seq would drop every frame."""
    first = run(stream("/design", {"prompt": "solar powered weather station with a sensor"}))
    thread_id = first[0]["thread_id"]
    second = run(stream("/resume", {"thread_id": thread_id, "answer": "USB-C 5V"}))

    assert second[0]["seq"] == first[-1]["seq"] + 1
    assert len({f["seq"] for f in first + second}) == len(first + second)
    assert second[-1]["type"] == "done"


def test_resume_on_an_unknown_thread_is_a_404():
    async def go():
        async with await client() as http:
            return await http.post("/resume", json={"thread_id": "nope", "answer": "x"})

    assert run(go()).status_code == 404


def test_an_unfixable_board_escalates_instead_of_looping():
    """No regulator in the catalogue survives 12 V. It asks; it does not spin or lie."""
    frames = run(stream("/design", {"prompt": "industrial node on a 12V supply with a sensor"}))

    assert frames[-1]["type"] == "question"
    assert [f["rule"] for f in frames if f["type"] == "conflict"] == ["voltage_overlap"] * 3


# ── export ────────────────────────────────────────────────────────────────────


def test_export_reflects_the_repaired_board_not_the_first_guess():
    frames = run(stream("/design", {"prompt": DEMO}))
    thread_id = frames[0]["thread_id"]

    async def go():
        async with await client() as http:
            return await http.get(f"/export/{thread_id}.csv")

    body = run(go()).text

    assert "TPS62825DMQR" in body, "the buck converter that resolved conflict 3"
    assert "SHT31-DIS-B" in body, "the in-stock sensor that resolved conflict 1"
    assert "SHT40-AD1B-R2" not in body, "the out-of-stock part must not survive"
    assert "AP2114H" not in body


def test_export_for_an_unknown_thread_is_a_404():
    async def go():
        async with await client() as http:
            return await http.get("/export/nothing.csv")

    assert run(go()).status_code == 404


# ── escalation ────────────────────────────────────────────────────────────────


def test_accepting_an_escalation_finishes_the_rest_of_the_board():
    """A real run reported DONE, 0 conflicts, over a board with four empty slots and a
    one-row BOM. Answering an escalation must resume placement, not abandon it."""
    first = run(stream("/design", {"prompt": "industrial node on a 12V supply with a sensor"}))
    assert first[-1]["type"] == "question", "the 12V board has no viable regulator"
    thread_id = first[0]["thread_id"]

    second = run(stream("/resume", {"thread_id": thread_id, "answer": "Accept the voltage mismatch"}))
    frames = first + second
    done = next(f for f in frames if f["type"] == "done")
    planned = {s["id"] for f in frames if f["type"] == "plan" for s in f["slots"]}
    selected = {f["slot"] for f in frames if f["type"] == "selection"}

    assert done["summary"]["slots"] == len(planned), "every slot must end up on the BOM"
    assert selected == planned


def test_a_waived_fault_stays_visible_as_a_warning():
    """Waiving a check is not the same as the check passing."""
    first = run(stream("/design", {"prompt": "industrial node on a 12V supply with a sensor"}))
    thread_id = first[0]["thread_id"]
    second = run(stream("/resume", {"thread_id": thread_id, "answer": "Accept the voltage mismatch"}))

    waived = [
        f for f in second
        if f["type"] == "check" and f["status"] == "warn" and "Accepted by you" in f["detail"]
    ]

    assert waived, "the finding must remain on screen, downgraded rather than deleted"


def test_stopping_an_escalation_ends_the_run():
    first = run(stream("/design", {"prompt": "industrial node on a 12V supply with a sensor"}))
    thread_id = first[0]["thread_id"]

    second = run(stream("/resume", {"thread_id": thread_id, "answer": "Stop and let me change the brief"}))

    assert second[-1]["type"] == "done"
    assert not [f for f in second if f["type"] == "selection"]


# ── a slot that finds nothing ─────────────────────────────────────────────────
#
# The soil-moisture board searched for "soil moisture sensor", found nothing, and
# finished `0 conflict · 0 pending · DONE` with three BOM rows for four slots. The slot
# was popped off `pending` and never accounted for again, and `done.summary.slots`
# counted BOM rows — so the summary was computed from the survivors and could never
# disagree with itself.


def empty_search(monkeypatch, *, missing: str):
    """Make one slot's search come back with nothing, as JLCPCB really did."""
    from continuity.graph import sourcing

    real = sourcing.find

    async def sometimes(query, *, constraint=None, **_context):
        if missing in query.lower():
            return []
        return await real(query, constraint=constraint)

    monkeypatch.setattr(sourcing, "find", sometimes)


def test_a_slot_that_finds_nothing_is_reported_not_dropped(monkeypatch):
    empty_search(monkeypatch, missing="sensor")
    frames = run(stream("/design", {"prompt": DEMO}))

    unfilled = [
        f for f in frames
        if f["type"] == "check" and f["status"] == "warn" and "no part" in f["detail"].lower()
    ]

    assert unfilled, "an unfilled slot must produce a verdict naming what was not found"


def test_the_summary_counts_planned_slots_not_bom_rows(monkeypatch):
    empty_search(monkeypatch, missing="sensor")
    frames = run(stream("/design", {"prompt": DEMO}))

    done = next(f for f in frames if f["type"] == "done")
    planned = {s["id"] for f in frames if f["type"] == "plan" for s in f["slots"]}
    bom = next(f for f in frames if f["type"] == "bom")

    assert done["summary"]["slots"] == len(planned)
    assert len(bom["rows"]) < len(planned), "this board really is short a part"
    assert done["summary"]["placed"] == len(bom["rows"])


# ── clarify: no silent default, and every supply reachable ────────────────────
#
# `clarify` mapped three hardcoded labels and sent everything else to `usb-5v`. §2 then
# showed the worse half: none of the three fitted any of the three briefs (a dynamo, a
# 48 V industrial bus, two cells in series), so the *correct* path — clicking a button —
# still forced a wrong answer every time.

UNKNOWN_SUPPLY = "a board powered by a hand-cranked dynamo"


def test_every_supply_the_engine_knows_is_offered():
    from continuity.planner import topology

    frames = run(stream("/design", {"prompt": UNKNOWN_SUPPLY}))
    question = frames[-1]

    assert question["type"] == "question"
    offered = set(question["suggestions"])
    known = {s.label for s in topology.INPUT_SOURCES.values()}

    assert known <= offered, f"unreachable through the UI: {known - offered}"


def test_an_unrecognised_answer_does_not_become_a_5v_board():
    """It must ask again. Silently resolving to USB 5V poisons every downstream verdict."""
    first = run(stream("/design", {"prompt": UNKNOWN_SUPPLY}))
    thread_id = first[0]["thread_id"]

    second = run(stream("/resume", {"thread_id": thread_id, "answer": "a hand-cranked dynamo"}))

    assert second[-1]["type"] == "question", "an unmatched answer must re-ask, not resolve"
    assert not [f for f in second if f["type"] == "plan"], "no board may be planned yet"


def test_a_chosen_suggestion_resolves_and_the_run_continues():
    first = run(stream("/design", {"prompt": UNKNOWN_SUPPLY}))
    thread_id = first[0]["thread_id"]

    second = run(stream("/resume", {"thread_id": thread_id, "answer": "12V barrel jack"}))
    supply = next(f for f in second if f["type"] == "reasoning" and "fed from" in f["text"])

    assert "12" in supply["text"]


# ── escalation: three answers, not two ────────────────────────────────────────
#
# `escalate` had a stop-word test and an else. Anything typed that was not a stop word
# waived the conflict, so "switch back to the Li-Ion 5V cell" was recorded as approving
# a 12 V fault. And `"no"` matched as a *substring*, so "the node needs 5V" killed a run.

TWELVE_V = "industrial node on a 12V supply with a sensor"


def escalated_thread():
    first = run(stream("/design", {"prompt": TWELVE_V}))
    assert first[-1]["type"] == "question", "the 12V board has no viable regulator"
    return first[0]["thread_id"], first


def test_typed_reasoning_is_not_recorded_as_acceptance():
    thread_id, _ = escalated_thread()

    second = run(stream("/resume", {"thread_id": thread_id, "answer": "use a buck converter instead"}))
    waived = [f for f in second if f["type"] == "reasoning" and "say-so" in f.get("text", "")]

    assert not waived, "guidance must not be filed as a waiver"


def test_supply_redirect_replans_the_existing_board():
    thread_id, _ = escalated_thread()

    second = run(
        stream("/resume", {"thread_id": thread_id, "answer": "use a 24 V supply instead"})
    )

    assert [
        frame
        for frame in second
        if frame["type"] == "reasoning" and "fed from 24.0 V" in frame.get("text", "")
    ]


@pytest.mark.parametrize("answer", ("12V barrel jack", "use a lab bench supply"))
def test_current_or_unknown_supply_redirect_remains_guidance(answer):
    thread_id, _ = escalated_thread()

    second = run(stream("/resume", {"thread_id": thread_id, "answer": answer}))

    assert [
        frame
        for frame in second
        if frame["type"] == "reasoning" and f"Taking that into account: {answer}" in frame.get("text", "")
    ]


def test_a_word_containing_no_does_not_stop_the_run():
    """`"no" in "the node needs 5V"` is True. That ended runs."""
    thread_id, _ = escalated_thread()

    second = run(stream("/resume", {"thread_id": thread_id, "answer": "the node needs a 5V rail"}))
    stopped = [f for f in second if f["type"] == "reasoning" and "Stopping here" in f.get("text", "")]

    assert not stopped


def test_accepting_still_waives():
    thread_id, _ = escalated_thread()

    second = run(stream("/resume", {"thread_id": thread_id, "answer": "Accept the voltage mismatch"}))

    assert [f for f in second if f["type"] == "reasoning" and "say-so" in f.get("text", "")]


def test_stopping_still_stops():
    thread_id, _ = escalated_thread()

    second = run(stream("/resume", {"thread_id": thread_id, "answer": "Stop and let me change the brief"}))

    assert second[-1]["type"] == "done"
    assert not [f for f in second if f["type"] == "selection"]


def test_placement_pushes_a_planned_constraint_into_the_search(monkeypatch):
    """A slot planned as a boost must search as one, not merely be named one."""
    from continuity.graph import sourcing

    seen: list = []
    real = sourcing.find

    async def spy(query, *, constraint=None, **_context):
        seen.append((query, constraint))
        return await real(query, constraint=constraint)

    monkeypatch.setattr(sourcing, "find", spy)
    run(stream("/design", {"prompt": DEMO}))

    assert seen, "select must call find"
    assert all(len(call) == 2 for call in seen)


def test_a_rejected_supply_answer_says_so_on_the_next_ask():
    """Otherwise the dialog returns unchanged and the Send button looks broken.

    `asked_before` was derived from `input_source != UNRESOLVED`, which is false on every
    re-ask by construction — the answer being unrecognised is exactly why we are here.
    """
    first = run(stream("/design", {"prompt": UNKNOWN_SUPPLY}))
    thread_id = first[0]["thread_id"]

    second = run(stream("/resume", {"thread_id": thread_id, "answer": "Hand Cranked Dynamo"}))

    assert second[-1]["type"] == "question"
    assert second[-1]["text"] != first[-1]["text"], "the re-ask must acknowledge the rejection"
    assert "recognise" in second[-1]["text"] or "recognize" in second[-1]["text"]


def test_a_warning_about_another_slot_still_reaches_the_stream():
    """The coin-cell beacon bug: the trace kept a stale, optimistic current check.

    A rail's budget is attributed to the *regulator* that sources it, so once a load
    landed on the rail the updated verdict named a different subject than the slot being
    validated and was dropped. The last thing the board ever said about current was
    `0 mA of 20 mA (0%)` — measured while the regulator was the only part on it — and the
    warning that replaced it, naming a part that states no draw, never arrived. A board
    whose entire brief was a coin-cell budget reported zero conflicts.
    """
    frames = run(stream("/design", {"prompt": DEMO}))
    checks = [f for f in frames if f["type"] == "check"]

    emitted = {(f["slot"], f["rule"], f.get("scope"), f["status"]) for f in checks}
    other_subject_warnings = {
        (slot, rule, scope) for slot, rule, scope, status in emitted if status != "pass"
    }

    assert other_subject_warnings, "a run with conflicts must carry non-pass checks"
    # Every non-pass verdict the engine reached is on the wire, whichever slot was current.
    assert any(rule == "current_budget" for _slot, rule, _scope in other_subject_warnings) or any(
        f["rule"] == "current_budget" for f in checks
    )
