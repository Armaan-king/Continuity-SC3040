import asyncio
import os
from dataclasses import replace

import pytest

from continuity.api.revision_store import commit_revision
from continuity.designs import service
from continuity.designs.context import ExecutionContext, current, stage
from tests.test_projects import a_store, signed_in
from tests.test_verified_architecture import sensor_board

pytestmark = pytest.mark.skipif(not os.environ.get("CONTINUITY_TEST_DB"), reason="requires disposable PostgreSQL")


async def seed(store, http):
    user = await store.user_by_email("sparsh@example.com")
    project = await store.create_project(user.id, "Revision reference")
    await store.create_thread("revision-reference", project.id, user.id, "3.3 V sensor")
    await store.save_progress("revision-reference", 0, "done")
    async with store.pool.connection() as conn:
        async with conn.transaction():
            revision = await commit_revision(conn, "revision-reference", service.document(sensor_board(voltage=3.3)), "Reference fixture")
    return user, project, revision


def test_revision_edit_export_and_ownership():
    async def go():
        async with a_store() as store, signed_in() as http:
            user, project, original = await seed(store, http)
            before = (await http.get(f"/revisions/{original}")).json()
            response = await http.post(f"/revisions/{original}/requirements", json={"expected_revision": original, "input_voltage": 5})
            assert response.status_code == 200, response.text
            updated = response.json()["revision_id"]
            after = (await http.get(f"/revisions/{updated}")).json()
            voltage = next(f for f in after["document"]["report"]["findings"] if f["rule"] == "voltage_overlap")
            assert voltage["outcome"] == "FAIL"
            assert (await http.get(f"/revisions/{original}")).json() == before
            assert after["parent_id"] == original
            stale = await http.post(f"/revisions/{original}/requirements", json={"expected_revision": original, "input_voltage": 4})
            assert stale.status_code == 409
            board = (await http.get("/threads/revision-reference/board")).json()
            assert board["revision_id"] == updated
            assert board["summary"]["failures"] >= 1
            trace_voltage = next(e for e in board["trace"] if e["type"] == "check" and e["rule"] == "voltage_overlap")
            assert trace_voltage["status"] == "fail"
            assert trace_voltage["detail"] == voltage["detail"]
            exported = await http.get(f"/revisions/{updated}/bom.csv")
            assert updated in exported.text and "REFERENCE-SENSOR" in exported.text
            assert (await http.get(f"/revisions/{updated}/report.json")).json()["revision_id"] == updated
            restored = await http.post(f"/revisions/{original}/restore", json={"expected_revision": updated})
            assert restored.status_code == 200, restored.text
            restored_id = restored.json()["revision_id"]
            assert restored_id not in {original, updated}
            restored_document = (await http.get(f"/revisions/{restored_id}")).json()
            assert restored_document["parent_id"] == updated
            restored_board = (await http.get("/threads/revision-reference/board")).json()
            assert restored_board["revision_id"] == restored_id
            assert next(e for e in restored_board["trace"] if e["type"] == "check" and e["rule"] == "voltage_overlap")["status"] == "pass"
            assert restored_document["document"]["board"]["requirements"]["input_voltage"] == before["document"]["board"]["requirements"]["input_voltage"]
            assert (await http.get(f"/revisions/{original}")).json() == before
            async with signed_in("other@example.com") as other:
                for path in (f"/revisions/{updated}", f"/revisions/{updated}/report.json", f"/projects/{project.id}/revisions"):
                    assert (await other.get(path)).status_code == 404
            await store.delete_project(project.id, user.id)
            assert await store.revision_for_user(updated, user.id) is None
    asyncio.run(go())


def test_answering_review_question_confirms_requirement_without_rewriting_history():
    async def go():
        async with a_store() as store, signed_in() as http:
            _, _, original = await seed(store, http)
            before = (await http.get(f"/revisions/{original}")).json()
            assert 'temp_range' in {q['field'] for q in before['guidance']['questions']}
            saved = await http.post(f"/revisions/{original}/requirements", json={
                'expected_revision': original, 'temp_range': [-10, 60]})
            assert saved.status_code == 200
            after = (await http.get(f"/revisions/{saved.json()['revision_id']}")).json()
            assert 'temp_range' not in {q['field'] for q in after['guidance']['questions']}
            requirement = next(r for r in after['document']['requirements'] if r['field'] == 'temp_range')
            assert requirement['basis'] == 'confirmed'
            assert requirement['value'] == [-10, 60]
            assert (await http.get(f"/revisions/{original}")).json() == before
            stale = await http.post(f"/revisions/{original}/requirements", json={
                'expected_revision': original, 'lifetime_hours': 72})
            assert stale.status_code == 409
    asyncio.run(go())


def test_unknown_answers_and_supply_selection_are_validated():
    async def go():
        async with a_store() as store, signed_in() as http:
            _, _, original = await seed(store, http)
            for changes in ({'temp_range': [70, 0]}, {'supply_capacity_mah': -1}, {'input_source': 'invented'}, {'current_margin': None}):
                response = await http.post(f"/revisions/{original}/requirements", json={'expected_revision': original, **changes})
                assert response.status_code == 422
            response = await http.post(f"/revisions/{original}/requirements", json={
                'expected_revision': original, 'input_source': 'battery-3v7'})
            assert response.status_code == 200
            after = (await http.get(f"/revisions/{response.json()['revision_id']}")).json()
            assert after['document']['board']['rails']['input']['voltage'] == 3.7
            assert after['document']['board']['requirements']['supply_capacity_mah'] is None
    asyncio.run(go())


def test_committed_stages_survive_a_new_execution_context():
    async def go():
        async with a_store() as store, signed_in() as http:
            await seed(store, http)
            calls = 0
            async def work():
                nonlocal calls
                calls += 1
                return {"source": "saved result"}
            for _ in range(2):
                token = current.set(ExecutionContext("revision-reference", store))
                try:
                    assert await stage("reference", {"input": 1}, work) == {"source": "saved result"}
                finally:
                    current.reset(token)
            assert calls == 1
    asyncio.run(go())


def test_research_reservations_enforce_budget_across_restarts():
    async def go():
        async with a_store() as store, signed_in() as http:
            await seed(store, http)
            assert await store.reserve_research("revision-reference", "first", 2, 0.04)
            assert not await store.reserve_research("revision-reference", "first", 2, 0.04)
            assert await store.reserve_research("revision-reference", "second", 2, 0.04)
            assert not await store.reserve_research("revision-reference", "third", 2, 0.04)
    asyncio.run(go())


@pytest.mark.parametrize("automatic", [False, True])
def test_interrupted_bom_reuses_committed_work_and_finishes(automatic, monkeypatch):
    from continuity.api import bom, events
    from continuity.api.app import app
    from continuity.api.recovery import recover
    from tests.test_normalize import CANDIDATE
    counts = {"resolve": 0, "normalize": 0}

    async def resolve(_row):
        counts["resolve"] += 1
        return CANDIDATE

    async def normalize(_candidate):
        counts["normalize"] += 1
        return sensor_board().part("sensor")

    async def interrupted(_parts):
        raise asyncio.CancelledError()

    async def rails(_parts):
        return []

    monkeypatch.setattr(bom, "_resolve", resolve)
    monkeypatch.setattr(bom.normalize, "normalize", normalize)
    monkeypatch.setattr(bom, "infer_rails", interrupted)

    async def go():
        async with a_store() as store, signed_in() as http:
            user = await store.user_by_email("sparsh@example.com")
            project = await store.create_project(user.id, "Interrupted BOM")
            payload = {"rows": [{"mpn": CANDIDATE.mpn, "qty": 3, "reference": "U1"}], "brief": None}
            await store.create_thread("interrupted-bom", project.id, user.id, "BOM", run_kind="bom", run_payload=payload)
            token = current.set(ExecutionContext("interrupted-bom", store))
            try:
                with pytest.raises(asyncio.CancelledError):
                    async for _ in bom.validate_bom([bom.BomRow(**payload["rows"][0])], events.EventStream("interrupted-bom")):
                        pass
            finally:
                current.reset(token)
            await store.save_progress("interrupted-bom", 7, "abandoned")
            monkeypatch.setattr(bom, "infer_rails", rails)
            if automatic:
                task = asyncio.create_task(recover(app))
                try:
                    async with asyncio.timeout(5):
                        while (await store.thread_for_user("interrupted-bom", user.id)).status != "done":
                            await asyncio.sleep(0.02)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            else:
                response = await http.post("/threads/interrupted-bom/continue")
                assert response.status_code == 200, response.text
            thread = await store.thread_for_user("interrupted-bom", user.id)
            assert thread.status == "done"
            revision = await store.thread_revision(thread.id, user.id)
            assert revision["document"]["bom"]["rows"][0]["qty"] == 3
            assert counts == {"resolve": 1, "normalize": 1}
            frames = await store.run_events(thread.id)
            assert min(frame["seq"] for frame in frames) > 7
            board = next(frame for frame in frames if frame["type"] == "board")
            assert board["revision_id"] == revision["id"] and "document" not in board
    asyncio.run(go())
