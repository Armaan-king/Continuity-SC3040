"""The `seq` counter, and what has to survive a process restart.

`test_api.py` already checks that seq is monotonic within a run and continues across a
resume. Both of those hold because `STREAMS` keeps the counter in memory for the life of
the process — which is exactly the assumption persistence removes.

The client drops any frame at or below its high-water mark. So a stream that restarts its
numbering after a restart does not error, does not warn, and does not render: every frame
of the resumed run is discarded in silence and the run appears to hang. These tests pin
the two properties that make that impossible — a counter that can be *read* without being
consumed, and a stream that can be *started* from a number already issued.
"""

from __future__ import annotations

from fuze.api.events import EventStream
from fuze.engine.models import Edge
from tests.boards import slot
from tests import parts


def test_nothing_sent_yet_reads_as_minus_one():
    """Matches the client's high-water mark, which initialises to -1 so seq 0 survives."""
    stream = EventStream("t1")

    assert stream.last_seq == -1


def test_last_seq_reports_the_number_actually_issued():
    stream = EventStream("t1")

    first = stream.reasoning(None, "one")
    second = stream.reasoning(None, "two")

    assert first["seq"] == 0
    assert second["seq"] == 1
    assert stream.last_seq == 1


def test_reading_last_seq_does_not_consume_a_number():
    """It was an `itertools.count`, which cannot be inspected without advancing it."""
    stream = EventStream("t1")
    stream.reasoning(None, "one")

    for _ in range(5):
        assert stream.last_seq == 0

    assert stream.reasoning(None, "two")["seq"] == 1


def test_a_stream_can_start_from_a_number_already_issued():
    stream = EventStream("t1", last_seq=7)

    assert stream.last_seq == 7
    assert stream.reasoning(None, "after a restart")["seq"] == 8


def test_a_restart_mid_thread_does_not_reissue_seen_numbers():
    """The whole point: the process dies between the interrupt and the resume."""
    before = EventStream("t1")
    seen = [before.reasoning(None, f"line {i}")["seq"] for i in range(4)]
    persisted = before.last_seq

    after = EventStream("t1", last_seq=persisted)
    resumed = [after.reasoning(None, f"resumed {i}")["seq"] for i in range(3)]

    assert min(resumed) > max(seen)
    assert len(set(seen + resumed)) == len(seen + resumed)


def test_a_fresh_stream_still_starts_at_zero():
    """The default must not shift — seq 0 is load-bearing for the client."""
    assert EventStream("t1").reasoning(None, "first")["seq"] == 0


def test_an_edge_patch_carries_a_power_edge_new_source():
    event = EventStream("t1").selection(
        "phy",
        parts.esp32s3(),
        "pending",
        [Edge("pwr-phy", "reg3", "phy", "3V3", "power", "pending")],
    )

    assert event["edges"] == [
        {"id": "pwr-phy", "from": "reg3", "label": "3V3", "status": "pending"}
    ]


def test_slot_added_uses_the_same_slot_and_edge_shapes_as_plan():
    stream = EventStream("t1")
    planned = stream.plan(
        [slot("mcu")],
        [Edge("pwr-mcu", "regulator", "mcu", "3V3", "power", "pending")],
    )
    added = stream.slot_added(
        slot("sensor"),
        [Edge("pwr-sensor", "regulator", "sensor", "3V3", "power", "pending")],
    )

    assert set(added["slot"]) == set(planned["slots"][0])
    assert set(added["edges"][0]) == set(planned["edges"][0])


def test_a_check_event_carries_whether_the_rule_could_be_evaluated():
    """Analytics counts unchecked evaluations from stored frames, so the frame must say so.

    `warn` covers both a genuine concern and a figure that was never published. Telling
    them apart from the wording would break the first time a detail string is reworded.
    """
    from fuze.engine.models import Verdict

    stream = EventStream("t1")
    missing = Verdict(rule="pin_budget", status="warn", detail="no GPIO count", subject="mcu", unchecked=True)
    concern = Verdict(rule="current_budget", status="warn", detail="83%", subject="reg")

    assert stream.check(missing)["unchecked"] is True
    assert stream.check(concern)["unchecked"] is False
