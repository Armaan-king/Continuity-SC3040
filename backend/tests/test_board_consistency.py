from continuity.api.boards import recorded_board
from continuity.api.events import EventStream
from continuity.engine.models import Verdict
from tests import parts
from tests.boards import slot


def test_final_snapshot_retains_failures_and_counts_unchecked_separately():
    stream = EventStream("board")
    part = parts.sht31()
    placed = slot("sensor").with_part(part)
    verdicts = [
        Verdict(rule="temperature_rating", status="fail", detail="Too cold", subject="sensor", involved=("sensor",)),
        Verdict(rule="pin_budget", status="warn", detail="No pin data", subject="sensor", unchecked=True),
        Verdict(rule="availability", status="warn", detail="Low stock", subject="sensor"),
    ]
    final = stream.board([placed], [], verdicts)
    done = stream.done(1, 1, 0, verdicts=verdicts, stopped=True)
    assert final["slots"][0]["status"] == "conflict"
    assert final["conflicts"][0]["subject"] == "sensor"
    assert done["summary"]["failures"] == 1
    assert done["summary"]["warnings"] == 1
    assert done["summary"]["unchecked"] == 1
    assert done["summary"]["stopped"] is True
    restored = recorded_board([final, done])
    assert restored["slots"] == final["slots"]
    assert restored["conflicts"] == final["conflicts"]


def test_legacy_bom_trace_restores_parts_failures_and_later_passes():
    stream = EventStream("legacy")
    part = parts.sht31()
    verdict = Verdict(rule="temperature_rating", status="fail", detail="Too cold", subject="sensor", involved=("sensor",))
    trace = [stream.plan([slot("sensor")], []), stream.selection("sensor", part, "conflict"),
             stream.check(verdict), stream.conflict(verdict)]
    assert recorded_board(trace)["slots"][0]["part"]["mpn"] == part.mpn
    assert recorded_board(trace)["slots"][0]["status"] == "conflict"
    trace.append(stream.check(Verdict(rule="temperature_rating", status="pass", detail="OK", subject="sensor")))
    assert recorded_board(trace)["slots"][0]["status"] == "pass"
    assert recorded_board(trace)["conflicts"] == []


def test_legacy_candidate_is_not_shown_as_valid_before_validation():
    stream = EventStream("unfinished")
    trace = [stream.plan([slot("sensor")], []), stream.candidate("sensor", parts.sht31())]
    assert recorded_board(trace)["slots"][0]["status"] == "pending"


def test_changed_pass_for_another_slot_replaces_its_previous_warning(monkeypatch):
    from continuity.graph import nodes
    from continuity.engine.models import Requirements
    earlier = Verdict(rule="current_budget", status="warn", detail="Near limit", subject="regulator", scope="3V3")
    final = Verdict(rule="current_budget", status="pass", detail="Within budget", subject="regulator", scope="3V3")
    monkeypatch.setattr(nodes.rules, "evaluate", lambda board: [final])
    emitted = []
    monkeypatch.setattr(nodes, "_emit", emitted.append)
    state = {"requirements": Requirements(), "slots": {}, "rails": {},
             "current": "sensor", "verdicts": [earlier]}
    nodes.validate(state, {"configurable": {"events": EventStream("changed-pass")}})
    assert any(event["type"] == "check" and event["slot"] == "regulator" and event["status"] == "pass"
               for event in emitted)
