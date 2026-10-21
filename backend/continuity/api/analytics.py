"""DS-16 validation analytics: how the engine has behaved across an engineer's runs.

Four views, each computed when requested from records the system already holds:

- rule outcomes — every `check` frame in `run_events`, bucketed pass / warn / fail /
  unchecked per rule;
- repair effectiveness — `findings` that carry a repair action, and whether it held;
- recurring conflicts — `findings` grouped by their engine-owned `signature`;
- supply exposure — the parts on finished boards and the lifecycle each was selected at.

## What this module will not do

Nothing here is written back. A verdict depends on the board that produced it, so a
stored score would put a board-dependent figure where facts about a part belong, and it
would drift from the records it was computed from. The queries run inside a read-only
transaction, so that is enforced by the database rather than by care.

No rate is reported over a denominator that is mostly unknown. Every rate carries its
numerator, denominator and unchecked count; when unchecked evaluations exceed
`UNCHECKED_THRESHOLD_PERCENT` of the denominator the rate is replaced by the reason it
was withheld. A withheld rate has no `value` key at all — never a null that a client
could render as zero (SRS 6.12, 7.6).

The queries and the route live in `analytics_api`; this module is pure, so every
figure can be tested without a database.

Only completed runs count. Abandoned, errored and in-flight runs are reported as
excluded counts, and the guided walkthrough is a replayed demonstration rather than the
engineer's own design, so it is left out entirely.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

UNCHECKED_THRESHOLD_PERCENT = 20
"""Above this share of unchecked evaluations a rate is withheld. SRS OI-22 proposes 20%."""

UNCHECKED_THRESHOLD = UNCHECKED_THRESHOLD_PERCENT / 100

SIGNATURE_LIMIT = 10
"""How many recurring conflict signatures are listed, most widespread first."""

LIFECYCLES = ("active", "nrnd", "obsolete", "unknown")
AT_RISK = frozenset({"nrnd", "obsolete"})
IN_PROGRESS = frozenset({"running", "awaiting"})


# ── records, as the queries return them ───────────────────────────────────────


@dataclass(frozen=True)
class CheckRow:
    thread_id: str
    seq: int
    slot: str
    rule: str
    status: str
    unchecked: bool


@dataclass(frozen=True)
class FindingRow:
    thread_id: str
    project_id: str
    rule: str
    slot: str
    outcome: str
    action: str | None
    signature: str | None
    worked: bool


@dataclass(frozen=True)
class SelectionRow:
    thread_id: str
    seq: int
    slot: str
    mpn: str
    lifecycle: str | None


@dataclass(frozen=True)
class BomItem:
    thread_id: str
    mpn: str
    distributor: str | None
    lifecycle: str | None


@dataclass(frozen=True)
class Records:
    runs: Mapping[str, int]
    checks: Sequence[CheckRow]
    findings: Sequence[FindingRow]
    selections: Sequence[SelectionRow]
    bom: Sequence[BomItem]


# ── the one way a rate is reported ────────────────────────────────────────────


def rate(numerator: int, denominator: int, unchecked: int) -> dict[str, Any]:
    """A rate with the counts it came from, or those counts and why it was withheld."""
    counts: dict[str, Any] = {
        "numerator": numerator,
        "denominator": denominator,
        "unchecked": unchecked,
    }
    if denominator == 0:
        return {**counts, "withheld": "Nothing has been recorded yet, so there is no rate to report."}
    if unchecked * 100 > denominator * UNCHECKED_THRESHOLD_PERCENT:
        share = round(unchecked * 100 / denominator)
        return {
            **counts,
            "withheld": (
                f"{unchecked} of {denominator} ({share}%) could not be evaluated, above the "
                f"{UNCHECKED_THRESHOLD_PERCENT}% limit for reporting a rate."
            ),
        }
    return {**counts, "value": round(numerator / denominator, 4)}


# ── view 1 · rule outcome distribution ────────────────────────────────────────


def _bucket(row: CheckRow) -> str:
    # A status this analysis does not know cannot be read as any decided outcome.
    if row.unchecked or row.status not in ("pass", "warn", "fail"):
        return "unchecked"
    return row.status


def rule_outcomes(checks: Iterable[CheckRow]) -> list[dict[str, Any]]:
    """Every evaluation a rule made, by outcome. The failure rate keeps unchecked in."""
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in checks:
        counts[row.rule][_bucket(row)] += 1

    rows = []
    for rule, bucket in counts.items():
        evaluations = sum(bucket.values())
        rows.append(
            {
                "rule": rule,
                "pass": bucket["pass"],
                "warn": bucket["warn"],
                "fail": bucket["fail"],
                "unchecked": bucket["unchecked"],
                "evaluations": evaluations,
                "failure_rate": rate(bucket["fail"], evaluations, bucket["unchecked"]),
            }
        )
    return sorted(rows, key=lambda row: (-row["fail"], -row["evaluations"], row["rule"]))


# ── view 2 · repair effectiveness by action ───────────────────────────────────


def _final_checks(checks: Iterable[CheckRow]) -> dict[tuple[str, str, str], CheckRow]:
    """The last evaluation of each (run, slot, rule) — what the finished board says."""
    final: dict[tuple[str, str, str], CheckRow] = {}
    for row in sorted(checks, key=lambda r: (r.thread_id, r.seq)):
        final[(row.thread_id, row.slot, row.rule)] = row
    return final


def repair_effectiveness(
    findings: Iterable[FindingRow], checks: Iterable[CheckRow]
) -> list[dict[str, Any]]:
    """For each repair action, how many applications held after revalidation.

    `worked` is what the run recorded. It is overridden only in one direction: a repair
    whose final evaluation of the same rule was unchecked did not *hold*, it simply
    could not be shown to fail, so it moves to the unchecked count.
    """
    final = _final_checks(checks)
    tallies: dict[str, Counter[str]] = defaultdict(Counter)
    for item in findings:
        if item.action is None:
            continue
        last = final.get((item.thread_id, item.slot, item.rule))
        if last is not None and last.unchecked:
            tallies[item.action]["unchecked"] += 1
        elif item.worked and item.outcome == "repaired":
            tallies[item.action]["held"] += 1
        else:
            tallies[item.action]["not_held"] += 1

    rows = []
    for action, tally in tallies.items():
        applied = sum(tally.values())
        rows.append(
            {
                "action": action,
                "applied": applied,
                "held": tally["held"],
                "not_held": tally["not_held"],
                "unchecked": tally["unchecked"],
                "held_rate": rate(tally["held"], applied, tally["unchecked"]),
            }
        )
    return sorted(rows, key=lambda row: (-row["applied"], row["action"]))


# ── view 3 · recurring conflict signatures ────────────────────────────────────


def _best_repair(group: Sequence[FindingRow]) -> dict[str, Any] | None:
    held: Counter[str] = Counter()
    applied: Counter[str] = Counter()
    for item in group:
        if item.action is None:
            continue
        applied[item.action] += 1
        if item.worked and item.outcome == "repaired":
            held[item.action] += 1
    if not held:
        return None
    action = min(held, key=lambda name: (-held[name], -held[name] / applied[name], name))
    return {"action": action, "held": held[action], "applied": applied[action]}


def recurring_conflicts(findings: Iterable[FindingRow]) -> list[dict[str, Any]]:
    """Conflict shapes seen in more than one run, and the repair that most often held."""
    groups: dict[str, list[FindingRow]] = defaultdict(list)
    for item in findings:
        if item.signature:
            groups[item.signature].append(item)

    rows = []
    for signature, group in groups.items():
        runs = len({item.thread_id for item in group})
        if runs < 2:
            continue
        rows.append(
            {
                "signature": signature,
                "rule": group[0].rule,
                "occurrences": len(group),
                "runs": runs,
                "projects": len({item.project_id for item in group}),
                "best_repair": _best_repair(group),
            }
        )
    rows.sort(key=lambda row: (-row["runs"], -row["occurrences"], row["signature"]))
    return rows[:SIGNATURE_LIMIT]


# ── view 4 · supply exposure ──────────────────────────────────────────────────


def _lifecycle(value: str | None) -> str:
    return value if value in LIFECYCLES else "unknown"


def supply_exposure(
    bom: Iterable[BomItem], selections: Iterable[SelectionRow]
) -> dict[str, Any]:
    """Lifecycle mix over the distinct parts on finished boards.

    The lifecycle is the one the part was *selected* at in that run, read from the
    `selection` frame — BOM rows do not carry it. Where neither says, it is unknown,
    and unknown is the unchecked count of the at-risk rate rather than a quiet "active".
    """
    selected: dict[tuple[str, str], str | None] = {}
    for row in sorted(selections, key=lambda r: (r.thread_id, r.seq)):
        selected[(row.thread_id, row.mpn)] = row.lifecycle

    placements = 0
    status_by_part: dict[str, str] = {}
    distributors: set[str] = set()
    for item in bom:
        placements += 1
        if item.distributor:
            distributors.add(item.distributor)
        lifecycle = _lifecycle(selected.get((item.thread_id, item.mpn)) or item.lifecycle)
        # A part known in one run is known; never let a later unknown overwrite that.
        if status_by_part.get(item.mpn, "unknown") == "unknown":
            status_by_part[item.mpn] = lifecycle

    mix = Counter(status_by_part.values())
    lifecycle_counts = {name: mix[name] for name in LIFECYCLES}
    at_risk = sum(mix[name] for name in AT_RISK)
    return {
        "parts": len(status_by_part),
        "placements": placements,
        "lifecycle": lifecycle_counts,
        "at_risk_rate": rate(at_risk, len(status_by_part), mix["unknown"]),
        "single_source": _single_source(sorted(distributors), len(status_by_part)),
    }


def _single_source(distributors: list[str], parts: int) -> dict[str, Any]:
    """Withheld: one feed cannot say how many other places sell a part."""
    if parts == 0:
        reason = "No parts have been placed on a completed board yet."
    else:
        feeds = ", ".join(distributors) or "a single distributor feed"
        reason = (
            f"Every part is quoted from one distributor feed ({feeds}), so whether it "
            "could be bought anywhere else is not recorded."
        )
    return {"distributors": distributors, "withheld": reason}


# ── the whole response ───────────────────────────────────────────────────────


def build_analytics(records: Records) -> dict[str, Any]:
    runs = records.runs
    return {
        "threshold": UNCHECKED_THRESHOLD,
        "runs": {
            "completed": runs.get("done", 0),
            "excluded": {
                "abandoned": runs.get("abandoned", 0),
                "error": runs.get("error", 0),
                "in_progress": sum(runs.get(status, 0) for status in IN_PROGRESS),
            },
        },
        "rules": rule_outcomes(records.checks),
        "repairs": repair_effectiveness(records.findings, records.checks),
        "signatures": recurring_conflicts(records.findings),
        "supply": supply_exposure(records.bom, records.selections),
    }

