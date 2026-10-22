"""`Verdict.unchecked` separates "could not be evaluated" from a genuine warning.

DS-16 analytics reports unchecked evaluations in their own column and withholds a rate
when they dominate it. That only works if the flag is set where a figure was missing and
nowhere else — a waived failure or an empty search is a finding, not a gap in the data.
"""

from __future__ import annotations

from fuze.api.bom import _unchecked_rail_verdicts
from fuze.engine.models import Verdict
from fuze.graph.nodes import _apply_waivers
from tests.boards import slot
from tests import parts


def test_a_waived_failure_is_a_known_result_not_an_unchecked_one():
    failed = Verdict(rule="thermal_dissipation", status="fail", detail="too hot.", subject="reg")

    (waived,) = _apply_waivers([failed], [("thermal_dissipation", "reg")])

    assert waived.status == "warn"
    assert waived.unchecked is False


def test_a_bom_with_no_inferred_rails_reports_every_rail_rule_unchecked():
    verdicts = _unchecked_rail_verdicts({"u1": slot("u1", parts.esp32s3())})

    assert {v.rule for v in verdicts} == {"voltage_overlap", "current_budget", "thermal_dissipation"}
    assert all(v.status == "warn" and v.unchecked for v in verdicts)


def test_unchecked_defaults_to_false_so_existing_verdicts_keep_their_meaning():
    assert Verdict(rule="footprint", status="pass", detail="ok", subject="u1").unchecked is False


def test_unchecked_can_only_accompany_a_warn():
    """A failure is a decided result; marking one unchecked would hide it from the rate."""
    import pytest

    with pytest.raises(ValueError, match="unchecked"):
        Verdict(rule="footprint", status="fail", detail="x", subject="u1", unchecked=True)
    with pytest.raises(ValueError, match="unchecked"):
        Verdict(rule="footprint", status="pass", detail="x", subject="u1", unchecked=True)
