"""The seeded DS-16 corpus: deterministic, large enough, and exercising every branch.

The corpus is read here the way `analytics_api` reads the database — check and selection
frames from completed runs, findings from the real `FindingRecorder` — so a demonstration
that relies on a withheld rate or a recurring conflict cannot silently lose it.
"""

from __future__ import annotations

from fuze.api.analytics import (
    BomItem,
    CheckRow,
    FindingRow,
    Records,
    SelectionRow,
    build_analytics,
)
from tools.seed_analytics import findings_for, generate_corpus

EMAIL = "demo@fuze.dev"


def _records(runs) -> Records:
    statuses: dict[str, int] = {}
    checks, selections, findings, bom = [], [], [], []
    for run in runs:
        statuses[run.status] = statuses.get(run.status, 0) + 1
        if run.status != "done":
            continue
        for event in run.events:
            if event["type"] == "check":
                checks.append(CheckRow(run.thread_id, event["seq"], event["slot"], event["rule"], event["status"], event["unchecked"]))
            elif event["type"] == "selection":
                selections.append(SelectionRow(run.thread_id, event["seq"], event["slot"], event["part"]["mpn"], event["part"]["lifecycle"]))
        for item in findings_for(run):
            findings.append(FindingRow(run.thread_id, run.project, item.rule, item.slot, item.outcome, item.action, item.signature, item.worked))
        bom += [BomItem(run.thread_id, row["mpn"], row["distributor"], None) for row in run.bom]
    return Records(statuses, checks, findings, selections, bom)


def test_the_corpus_is_identical_on_every_generation():
    first = generate_corpus(EMAIL)
    second = generate_corpus(EMAIL)

    assert [run.events for run in first] == [run.events for run in second]


def test_the_corpus_has_at_least_twenty_completed_runs_and_some_excluded_ones():
    report = build_analytics(_records(generate_corpus(EMAIL)))

    assert report["runs"]["completed"] >= 20
    assert report["runs"]["excluded"]["abandoned"] >= 1
    assert report["runs"]["excluded"]["error"] >= 1


def test_the_corpus_shows_both_a_reported_and_a_withheld_failure_rate():
    rules = build_analytics(_records(generate_corpus(EMAIL)))["rules"]

    assert any("value" in row["failure_rate"] for row in rules)
    assert any("withheld" in row["failure_rate"] for row in rules)


def test_the_corpus_has_repairs_that_held_and_repairs_that_did_not():
    repairs = build_analytics(_records(generate_corpus(EMAIL)))["repairs"]

    assert sum(row["held"] for row in repairs) > 0
    assert sum(row["not_held"] for row in repairs) > 0


def test_the_corpus_has_conflicts_recurring_across_projects():
    signatures = build_analytics(_records(generate_corpus(EMAIL)))["signatures"]

    assert len(signatures) >= 2
    assert any(row["projects"] >= 2 for row in signatures)
    assert any(row["best_repair"] is not None for row in signatures)


def test_the_corpus_has_parts_at_every_lifecycle_stage():
    supply = build_analytics(_records(generate_corpus(EMAIL)))["supply"]

    assert all(supply["lifecycle"][stage] > 0 for stage in ("active", "nrnd", "obsolete", "unknown"))
    assert "value" in supply["at_risk_rate"], "unknown parts stay under the 20% limit"


def test_every_frame_has_the_shape_the_live_stream_emits():
    for run in generate_corpus(EMAIL):
        seqs = [event["seq"] for event in run.events]
        assert seqs == list(range(len(seqs)))
        for event in run.events:
            if event["type"] == "check":
                assert event["status"] in ("pass", "warn", "fail")
                assert not (event["unchecked"] and event["status"] != "warn")


def test_the_demonstration_withholds_the_pin_budget_rate():
    """The demo script points at this row; a re-seed must not quietly move it."""
    rules = {row["rule"]: row for row in build_analytics(_records(generate_corpus(EMAIL)))["rules"]}

    assert "withheld" in rules["pin_budget"]["failure_rate"]
    assert "value" in rules["thermal_dissipation"]["failure_rate"]
