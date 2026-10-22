"""DS-16 validation analytics: four views over an engineer's completed runs.

The pure half — `rate` and the four view builders — is tested without a database. The
query half runs only with `FUZE_TEST_DB`, against a small corpus written through the
same `Store` methods a real run uses, so every figure here is one a direct query against
`findings` and `run_events` would reproduce.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import httpx
import pytest

from fuze.api import analytics
from fuze.api.analytics import (
    BomItem,
    CheckRow,
    FindingRow,
    Records,
    SelectionRow,
    build_analytics,
    rate,
    recurring_conflicts,
    repair_effectiveness,
    rule_outcomes,
    supply_exposure,
)
from fuze.api.app import app
from fuze.api.findings import Finding
from fuze.api.store import Store


def check(thread: str, seq: int, rule: str, status: str, *, slot: str = "u1", unchecked: bool = False):
    return CheckRow(thread, seq, slot, rule, status, unchecked)


def finding(
    thread: str,
    *,
    rule: str = "thermal_dissipation",
    slot: str = "regulator",
    action: str | None = "change_topology",
    worked: bool = True,
    outcome: str = "repaired",
    signature: str | None = "thermal_dissipation|regulator|linear",
    project: str = "p1",
):
    return FindingRow(thread, project, rule, slot, outcome, action, signature, worked)


# ── rate ──────────────────────────────────────────────────────────────────────


def test_a_rate_carries_its_numerator_denominator_and_unchecked_count():
    assert rate(3, 20, 2) == {"numerator": 3, "denominator": 20, "unchecked": 2, "value": 0.15}


def test_a_rate_at_exactly_the_threshold_is_still_reported():
    assert rate(1, 10, 2)["value"] == 0.1


def test_a_rate_over_the_unchecked_threshold_is_withheld_with_a_reason_and_no_value():
    withheld = rate(1, 10, 3)

    assert "value" not in withheld, "a withheld rate must never be a null shown as zero"
    assert withheld["numerator"] == 1 and withheld["denominator"] == 10
    assert withheld["unchecked"] == 3
    assert "3 of 10" in withheld["withheld"] and "20%" in withheld["withheld"]


def test_a_rate_over_nothing_is_withheld_rather_than_reported_as_zero():
    empty = rate(0, 0, 0)

    assert "value" not in empty
    assert empty["withheld"]


# ── rule outcome distribution ─────────────────────────────────────────────────


def test_rule_outcomes_count_every_evaluation_in_its_own_bucket():
    rows = rule_outcomes(
        [
            check("t1", 0, "current_budget", "pass"),
            check("t1", 1, "current_budget", "fail"),
            check("t1", 2, "current_budget", "warn"),
            check("t2", 0, "current_budget", "warn", unchecked=True),
            check("t2", 1, "current_budget", "pass"),
        ]
    )

    (budget,) = rows
    assert budget["rule"] == "current_budget"
    assert (budget["pass"], budget["warn"], budget["fail"], budget["unchecked"]) == (2, 1, 1, 1)
    assert budget["evaluations"] == 5
    assert budget["failure_rate"] == {"numerator": 1, "denominator": 5, "unchecked": 1, "value": 0.2}


def test_an_unchecked_evaluation_is_never_counted_as_a_pass_or_dropped_from_the_denominator():
    (row,) = rule_outcomes(
        [check("t1", 0, "pin_budget", "pass")]
        + [check("t1", n, "pin_budget", "warn", unchecked=True) for n in range(1, 4)]
    )

    assert row["pass"] == 1
    assert row["failure_rate"]["denominator"] == 4
    assert "value" not in row["failure_rate"], "3 of 4 unchecked is far over 20%"


def test_removing_a_field_moves_evaluations_into_unchecked_rather_than_changing_failures():
    """The DS-16 verification step from the Quality Plan, at the level of the counts."""
    with_field = [check("t1", 0, "pin_budget", "fail"), check("t2", 0, "pin_budget", "pass")]
    without_field = [
        check("t1", 0, "pin_budget", "warn", unchecked=True),
        check("t2", 0, "pin_budget", "pass"),
    ]

    (before,) = rule_outcomes(with_field)
    (after,) = rule_outcomes(without_field)

    assert (before["fail"], before["unchecked"]) == (1, 0)
    assert (after["fail"], after["unchecked"]) == (0, 1)
    assert after["evaluations"] == before["evaluations"]


def test_a_status_the_analysis_does_not_recognise_is_counted_unchecked_not_warn():
    (row,) = rule_outcomes([check("t1", 0, "footprint", "skipped")])

    assert (row["warn"], row["unchecked"]) == (0, 1)


def test_rules_are_ordered_by_how_often_they_fail():
    rows = rule_outcomes(
        [
            check("t1", 0, "footprint", "pass"),
            check("t1", 1, "thermal_dissipation", "fail"),
            check("t1", 2, "thermal_dissipation", "fail"),
            check("t1", 3, "availability", "fail"),
        ]
    )

    assert [row["rule"] for row in rows] == ["thermal_dissipation", "availability", "footprint"]


# ── repair effectiveness ──────────────────────────────────────────────────────


def test_repair_effectiveness_reports_the_share_of_each_action_that_held():
    rows = repair_effectiveness(
        [
            finding("t1", action="change_topology", worked=True),
            finding("t2", action="change_topology", worked=True),
            finding("t3", action="change_topology", worked=False),
            finding("t4", action="swap", worked=False, outcome="unresolved"),
            finding("t5", action=None, worked=False, outcome="unresolved"),
        ],
        [],
    )

    by_action = {row["action"]: row for row in rows}
    assert set(by_action) == {"change_topology", "swap"}, "a finding with no repair is not a repair"
    topology = by_action["change_topology"]
    assert (topology["applied"], topology["held"], topology["not_held"]) == (3, 2, 1)
    assert topology["held_rate"]["value"] == pytest.approx(2 / 3, abs=1e-4)


def test_a_repair_whose_revalidation_was_unchecked_is_not_counted_as_held():
    rows = repair_effectiveness(
        [finding("t1", action="swap", worked=True, slot="regulator", rule="current_budget")],
        [check("t1", 9, "current_budget", "warn", slot="regulator", unchecked=True)],
    )

    (swap,) = rows
    assert swap["held"] == 0
    assert swap["unchecked"] == 1
    assert "value" not in swap["held_rate"], "1 of 1 unchecked is withheld"


def test_the_final_check_decides_whether_a_repair_revalidated_unchecked():
    rows = repair_effectiveness(
        [finding("t1", action="swap", worked=True, slot="reg", rule="current_budget")],
        [
            check("t1", 3, "current_budget", "warn", slot="reg", unchecked=True),
            check("t1", 7, "current_budget", "pass", slot="reg"),
        ],
    )

    assert rows[0]["held"] == 1 and rows[0]["unchecked"] == 0


def test_no_repairs_reports_an_empty_list():
    assert repair_effectiveness([finding("t1", action=None, worked=False, outcome="unresolved")], []) == []


# ── recurring conflict signatures ─────────────────────────────────────────────


def test_a_signature_seen_in_two_runs_recurs_and_names_the_repair_that_most_often_held():
    rows = recurring_conflicts(
        [
            finding("t1", action="swap", worked=False),
            finding("t2", action="change_topology", worked=True),
            finding("t3", action="change_topology", worked=True, project="p2"),
            finding("t4", signature="availability|sensor|pkg:SOT", rule="availability"),
        ]
    )

    (thermal,) = rows
    assert thermal["signature"] == "thermal_dissipation|regulator|linear"
    assert thermal["rule"] == "thermal_dissipation"
    assert (thermal["occurrences"], thermal["runs"], thermal["projects"]) == (3, 3, 2)
    assert thermal["best_repair"] == {"action": "change_topology", "held": 2, "applied": 2}


def test_a_recurring_conflict_with_no_repair_that_held_says_so():
    rows = recurring_conflicts([finding("t1", worked=False), finding("t2", worked=False)])

    assert rows[0]["best_repair"] is None


def test_findings_without_a_signature_are_not_grouped():
    assert recurring_conflicts([finding("t1", signature=None), finding("t2", signature=None)]) == []


# ── supply exposure ───────────────────────────────────────────────────────────


def test_supply_exposure_counts_lifecycle_across_distinct_parts():
    exposure = supply_exposure(
        [
            BomItem("t1", "AP2112K", "JLCPCB", None),
            BomItem("t2", "AP2112K", "JLCPCB", None),
            BomItem("t1", "OLD-LDO", "JLCPCB", None),
            BomItem("t2", "MYSTERY", "JLCPCB", None),
        ],
        [
            SelectionRow("t1", 1, "reg", "AP2112K", "active"),
            SelectionRow("t1", 2, "ldo", "OLD-LDO", "obsolete"),
        ],
    )

    assert exposure["parts"] == 3
    assert exposure["placements"] == 4
    assert exposure["lifecycle"] == {"active": 1, "nrnd": 0, "obsolete": 1, "unknown": 1}
    assert exposure["at_risk_rate"]["numerator"] == 1
    assert exposure["at_risk_rate"]["unchecked"] == 1
    assert "value" not in exposure["at_risk_rate"], "1 of 3 unknown is over 20%"


def test_single_source_is_withheld_and_names_the_feed():
    exposure = supply_exposure([BomItem("t1", "AP2112K", "JLCPCB", "active")], [])

    single = exposure["single_source"]
    assert "value" not in single
    assert single["distributors"] == ["JLCPCB"]
    assert "JLCPCB" in single["withheld"]


def test_an_unrecognised_lifecycle_is_unknown_rather_than_active():
    exposure = supply_exposure([BomItem("t1", "X", "JLCPCB", "Discontinued?")], [])

    assert exposure["lifecycle"]["unknown"] == 1


# ── the whole response ────────────────────────────────────────────────────────


def test_an_engineer_with_no_completed_runs_gets_an_empty_report_not_an_error():
    report = build_analytics(Records(runs={}, checks=[], findings=[], selections=[], bom=[]))

    assert report["runs"]["completed"] == 0
    assert report["rules"] == [] and report["repairs"] == [] and report["signatures"] == []
    assert report["supply"]["parts"] == 0
    assert report["threshold"] == analytics.UNCHECKED_THRESHOLD


def test_runs_report_completed_and_excluded_counts():
    report = build_analytics(
        Records(
            runs={"done": 4, "abandoned": 2, "error": 1, "running": 1},
            checks=[],
            findings=[],
            selections=[],
            bom=[],
        )
    )

    assert report["runs"] == {"completed": 4, "excluded": {"abandoned": 2, "error": 1, "in_progress": 1}}


# ── the query half, against a real database ──────────────────────────────────

DB_URL = os.environ.get("FUZE_TEST_DB")
database = pytest.mark.skipif(not DB_URL, reason="set FUZE_TEST_DB to run analytics queries")


@asynccontextmanager
async def a_store():
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(DB_URL, min_size=1, max_size=2, open=False) as pool:
        await pool.open()
        store = Store(pool)
        await store.setup()
        async with pool.connection() as conn:
            await conn.execute("TRUNCATE users, sessions, projects, threads, part_facts CASCADE")
        previous = app.state.store
        app.state.store = store
        try:
            yield store
        finally:
            app.state.store = previous


async def _signed_in(email: str) -> tuple[httpx.AsyncClient, str]:
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    await client.post("/auth/register", json={"email": email, "password": "a-good-password"})
    me = (await client.get("/auth/me")).json()
    return client, me["id"]


async def _run(
    store: Store,
    user_id: str,
    project_id: str,
    thread_id: str,
    *,
    status: str = "done",
    checks: list[dict] = (),
    findings: list[Finding] = (),
    bom: list[dict] = (),
) -> None:
    """A run recorded exactly as `_run` records one: events, findings, BOM, then status."""
    await store.create_thread(thread_id, project_id, user_id, "brief")
    events = [
        {"type": "selection", "seq": 0, "slot": item["slot"], "part": {"mpn": item["mpn"], "lifecycle": item.get("lifecycle")}}
        for item in bom
    ]
    events += [{"type": "check", "seq": n + len(events), **frame} for n, frame in enumerate(checks)]
    for seq, event in enumerate(events):
        event["seq"] = seq
    await store.save_run_events(thread_id, events)
    await store.save_findings(thread_id, list(findings))
    await store.save_bom(thread_id, list(bom))
    await store.save_progress(thread_id, len(events) - 1, status)


def _check(rule: str, status: str, unchecked: bool = False, slot: str = "u1") -> dict:
    return {"slot": slot, "rule": rule, "scope": None, "status": status, "detail": "-", "unchecked": unchecked}


@database
def test_the_endpoint_requires_a_session():
    async def go():
        async with a_store():
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
            return await client.get("/analytics")

    assert asyncio.run(go()).status_code == 401


@database
def test_the_endpoint_counts_only_completed_runs_of_the_signed_in_engineer():
    async def go():
        async with a_store() as store:
            mine, me = await _signed_in("mine@example.com")
            theirs, them = await _signed_in("theirs@example.com")
            project = await store.create_project(me, "Weather")
            other = await store.create_project(them, "Theirs")
            await _run(store, me, project.id, "done-1", checks=[_check("footprint", "pass")])
            await _run(store, me, project.id, "done-2", checks=[_check("footprint", "fail")])
            await _run(store, me, project.id, "gave-up", status="abandoned", checks=[_check("footprint", "fail")])
            await _run(store, me, project.id, "broke", status="error", checks=[_check("footprint", "fail")])
            await _run(store, them, other.id, "theirs-1", checks=[_check("footprint", "fail")] * 1)
            return (await mine.get("/analytics")).json(), (await theirs.get("/analytics")).json()

    mine, theirs = asyncio.run(go())
    assert mine["runs"] == {"completed": 2, "excluded": {"abandoned": 1, "error": 1, "in_progress": 0}}
    (footprint,) = mine["rules"]
    assert (footprint["pass"], footprint["fail"], footprint["evaluations"]) == (1, 1, 2)
    assert theirs["runs"]["completed"] == 1
    assert theirs["rules"][0]["fail"] == 1


@database
def test_the_walkthrough_demo_is_not_counted_as_the_engineers_own_design():
    async def go():
        async with a_store() as store:
            mine, me = await _signed_in("mine@example.com")
            demo = await store.create_project(me, "Welcome", is_walkthrough=True)
            await _run(store, me, demo.id, "walk", checks=[_check("footprint", "fail")])
            return (await mine.get("/analytics")).json()

    report = asyncio.run(go())
    assert report["runs"]["completed"] == 0
    assert report["rules"] == []


@database
def test_the_endpoint_reports_all_four_views_from_stored_records():
    async def go():
        async with a_store() as store:
            mine, me = await _signed_in("mine@example.com")
            p1 = await store.create_project(me, "Weather")
            p2 = await store.create_project(me, "Logger")
            sig = "thermal_dissipation|regulator|linear|pkg:SOT"
            repaired = Finding(
                "thermal_dissipation", "regulator", "LDO", "Too hot.", "repaired",
                action="change_topology", replacement_mpn="BUCK", signature=sig, worked=True,
            )
            for project, thread in ((p1, "t1"), (p2, "t2")):
                await _run(
                    store, me, project.id, thread,
                    checks=[
                        _check("thermal_dissipation", "fail", slot="regulator"),
                        _check("thermal_dissipation", "pass", slot="regulator"),
                        _check("pin_budget", "warn", unchecked=True, slot="mcu"),
                    ],
                    findings=[repaired],
                    bom=[
                        {"slot": "regulator", "mpn": "BUCK", "distributor": "JLCPCB", "lifecycle": "active"},
                        {"slot": "mcu", "mpn": "ESP32", "distributor": "JLCPCB", "lifecycle": "nrnd"},
                    ],
                )
            return (await mine.get("/analytics")).json()

    report = asyncio.run(go())
    rules = {row["rule"]: row for row in report["rules"]}
    assert rules["thermal_dissipation"]["failure_rate"]["value"] == 0.5
    assert rules["pin_budget"]["unchecked"] == 2
    assert "value" not in rules["pin_budget"]["failure_rate"]
    (topology,) = report["repairs"]
    assert (topology["action"], topology["applied"], topology["held"]) == ("change_topology", 2, 2)
    (signature,) = report["signatures"]
    assert (signature["runs"], signature["projects"]) == (2, 2)
    assert signature["best_repair"]["action"] == "change_topology"
    assert report["supply"]["parts"] == 2
    assert report["supply"]["lifecycle"]["nrnd"] == 1
    assert report["supply"]["single_source"]["distributors"] == ["JLCPCB"]


@database
def test_viewing_the_analysis_writes_nothing():
    async def go():
        async with a_store() as store:
            mine, me = await _signed_in("mine@example.com")
            project = await store.create_project(me, "Weather")
            await _run(store, me, project.id, "t1", checks=[_check("footprint", "pass")])

            async def snapshot():
                async with store.pool.connection() as conn:
                    rows = []
                    for table in ("threads", "findings", "run_events", "projects", "part_facts"):
                        cursor = await conn.execute(f"SELECT md5(string_agg(t::text, ',' ORDER BY t::text)) FROM {table} t")
                        rows.append((await cursor.fetchone())[0])
                    return rows

            before = await snapshot()
            response = await mine.get("/analytics")
            return response.status_code, before, await snapshot()

    status, before, after = asyncio.run(go())
    assert status == 200
    assert before == after


def test_a_failed_retrieval_reports_that_the_analysis_could_not_load_and_returns_none_of_it():
    """UC-16.0.E.1 — no partial analysis, and a status the client can tell from success."""
    import psycopg

    from fuze.api.auth import current_user
    from fuze.api.store import User

    class _BrokenPool:
        def connection(self):
            raise psycopg.OperationalError("connection refused")

    class _BrokenStore:
        pool = _BrokenPool()

    async def go():
        previous = app.state.store
        app.state.store = _BrokenStore()
        app.dependency_overrides[current_user] = lambda: User("u1", "a@b.c", "h", None)
        try:
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
            return await client.get("/analytics")
        finally:
            app.state.store = previous
            app.dependency_overrides.pop(current_user, None)

    response = asyncio.run(go())
    assert response.status_code == 503
    assert "could not be loaded" in response.json()["detail"]
    assert "rules" not in response.json()
