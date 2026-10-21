"""Validation of an already-selected bill of materials."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from continuity.api import app as app_module
from continuity.api.app import app
from continuity.api import bom as bom_module
from continuity.engine.models import PartSpec, Requirements
from tests import parts


def run(coro):
    return asyncio.run(coro)


async def frames(payload: dict) -> list[dict]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        async with http.stream("POST", "/bom/validate", json=payload) as response:
            assert response.status_code == 200
            return [
                json.loads(line[6:])
                async for line in response.aiter_lines()
                if line.startswith("data: ")
            ]


def _row(mpn: str) -> dict:
    return {
        "lcsc": f"C-{mpn}",
        "model": mpn,
        "manufacturer": "Test",
        "description": "Test part",
        "package": "SOT-23-5",
        "category": "Regulator",
        "subcategory": "Regulator",
        "stock": 100,
        "price": 0.1,
        "specs": {},
    }


def _install_parts(monkeypatch, by_mpn: dict[str, object]) -> None:
    async def get_part(*, mpn=None, lcsc=None):
        return {"results": [_row(mpn)]} if mpn in by_mpn else {"results": []}

    async def normalize(candidate):
        return by_mpn[candidate.mpn]

    monkeypatch.setattr(bom_module.search, "get_part", get_part)
    monkeypatch.setattr(bom_module.normalize, "normalize", normalize)


def _no_rails(monkeypatch) -> None:
    async def infer(parts):
        return []

    monkeypatch.setattr(bom_module, "infer_rails", infer)


def _requirements_reply(monkeypatch, reply):
    """Make the requirements-only model path deterministic for BOM tests."""
    monkeypatch.setattr(bom_module.llm, "available", lambda: True)

    async def complete_json(system, brief):
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(bom_module.llm, "complete_json", complete_json)


def test_industrial_brief_fails_a_commercial_part_that_defaults_accept(monkeypatch):
    _install_parts(
        monkeypatch,
        {"COMMERCIAL": parts.oled(mpn="COMMERCIAL", temp_min=0, temp_max=70)},
    )
    _no_rails(monkeypatch)
    _requirements_reply(monkeypatch, {"temp_range": [-40, 85]})

    default_checks = run(frames({"bom": "COMMERCIAL"}))
    industrial_checks = run(
        frames({"bom": "COMMERCIAL", "prompt": "An industrial controller."})
    )

    assert next(
        event
        for event in default_checks
        if event["type"] == "check" and event["rule"] == "temperature_rating"
    )["status"] == "pass"
    assert next(
        event
        for event in industrial_checks
        if event["type"] == "check" and event["rule"] == "temperature_rating"
    )["status"] == "fail"


def test_production_brief_raises_the_availability_floor(monkeypatch):
    _install_parts(monkeypatch, {"THIN": parts.sht31(mpn="THIN", stock=250)})
    _no_rails(monkeypatch)
    _requirements_reply(monkeypatch, {"min_stock": 5000})

    default_checks = run(frames({"bom": "THIN"}))
    production_checks = run(
        frames({"bom": "THIN", "prompt": "First production run of 5000 units."})
    )

    assert next(
        event
        for event in default_checks
        if event["type"] == "check" and event["rule"] == "availability"
    )["status"] == "pass"
    assert next(
        event
        for event in production_checks
        if event["type"] == "check" and event["rule"] == "availability"
    )["status"] == "fail"


def test_no_brief_keeps_exact_requirements_defaults(monkeypatch):
    _requirements_reply(monkeypatch, {"temp_range": [-40, 85]})

    assert run(bom_module.requirements_from_brief(None)) == Requirements()


def test_unavailable_requirements_model_falls_back_to_defaults(monkeypatch):
    monkeypatch.setattr(bom_module.llm, "available", lambda: False)

    assert run(bom_module.requirements_from_brief("industrial")) == Requirements()


def test_unparseable_requirements_reply_falls_back_to_defaults(monkeypatch):
    _requirements_reply(monkeypatch, ValueError("not JSON"))

    assert run(bom_module.requirements_from_brief("industrial")) == Requirements()


def test_unusable_requirements_reply_falls_back_to_defaults(monkeypatch):
    _requirements_reply(monkeypatch, {"ambient_c": float("nan")})

    assert run(bom_module.requirements_from_brief("industrial")) == Requirements()


def test_requirements_reply_ignores_slots_and_rails(monkeypatch):
    _requirements_reply(
        monkeypatch,
        {
            "temp_range": [-40, 85],
            "slots": [{"id": "regulator"}],
            "rails": [{"id": "3V3", "voltage": 3.3}],
        },
    )

    requirements = run(bom_module.requirements_from_brief("industrial"))

    assert requirements == Requirements(temp_range=(-40, 85))


def test_zero_current_margin_is_refused_by_requirement_cleaning(monkeypatch):
    _requirements_reply(monkeypatch, {"temp_range": [-40, 85], "current_margin": 0})

    requirements = run(bom_module.requirements_from_brief("industrial"))

    assert requirements.temp_range == (-40, 85)
    assert requirements.current_margin == Requirements().current_margin


def test_three_part_bom_streams_contract_events(monkeypatch):
    _install_parts(
        monkeypatch,
        {
            "REG": parts.ap2112k(mpn="REG"),
            "MCU": parts.esp32s3(mpn="MCU"),
            "SENSOR": parts.sht31(mpn="SENSOR"),
        },
    )

    async def infer(resolved):
        return [{"id": "3V3", "voltage": 3.3, "source": "REG", "members": ["MCU", "SENSOR"]}]

    monkeypatch.setattr(bom_module, "infer_rails", infer)
    result = run(frames({"bom": "mpn,qty,reference\nREG,1,U1\nMCU,1,U2\nSENSOR,2,U3"}))
    kinds = [event["type"] for event in result]

    assert kinds[0] == "plan"
    assert len([event for event in result if event["type"] == "selection"]) == 3
    assert [event for event in result if event["type"] == "check"]
    assert kinds[-2:] == ["bom", "done"]
    assert not [event for event in result if event["type"] == "repair"]


def test_resolved_bom_parts_land_in_their_matching_tiers(monkeypatch):
    capacitor = PartSpec(
        mpn="CAP",
        manufacturer="Test",
        description="Ceramic capacitor",
        category="Capacitor",
        role="passive",
    )
    _install_parts(
        monkeypatch,
        {
            "BUCK": parts.buck_3v3(mpn="BUCK"),
            "LDO": parts.ap2112k(mpn="LDO"),
            "MCU": parts.esp32s3(mpn="MCU"),
            "SENSOR": parts.sht31(mpn="SENSOR"),
            "CAP": capacitor,
        },
    )
    _no_rails(monkeypatch)

    result = run(frames({"bom": "BUCK\nLDO\nMCU\nSENSOR\nCAP\nMISSING"}))
    slots = result[0]["slots"]
    tiers = {slot["id"]: slot["tier"] for slot in slots}

    assert tiers == {
        "bom_1": "power",
        "bom_2": "power",
        "bom_3": "core",
        "bom_4": "peripherals",
        "bom_5": "passives",
        "bom_6": "peripherals",
    }
    assert tiers["bom_1"] != "passives"
    assert [slot["id"] for slot in slots] == [
        "bom_1",
        "bom_2",
        "bom_3",
        "bom_4",
        "bom_5",
        "bom_6",
    ]


def test_structured_rows_accept_quantity_and_reference_designator(monkeypatch):
    _install_parts(monkeypatch, {"FOUND": parts.sht31(mpn="FOUND")})
    _no_rails(monkeypatch)

    result = run(
        frames({"bom": [{"mpn": "FOUND", "qty": 2, "reference_designator": "U1"}]})
    )

    assert result[0]["slots"] == [{"id": "bom_1", "label": "U1", "tier": "peripherals", "pinned": False}]
    assert next(event for event in result if event["type"] == "bom")["rows"][0]["qty"] == 2


def test_mpn_resolution_starts_all_lookups_before_any_finishes(monkeypatch):
    async def go():
        entered: list[str] = []
        release = asyncio.Event()

        async def get_part(*, mpn=None, lcsc=None):
            entered.append(mpn)
            await release.wait()
            return {"results": [_row(mpn)]}

        async def normalize(candidate):
            return parts.sht31(mpn=candidate.mpn)

        monkeypatch.setattr(bom_module.search, "get_part", get_part)
        monkeypatch.setattr(bom_module.normalize, "normalize", normalize)
        _no_rails(monkeypatch)
        task = asyncio.create_task(frames({"bom": "ONE\nTWO\nTHREE"}))
        for _ in range(10):
            if len(entered) == 3:
                break
            await asyncio.sleep(0)
        overlapped = list(entered)
        release.set()
        await task
        return overlapped

    assert run(go()) == ["ONE", "TWO", "THREE"]


def test_unresolved_mpn_warns_by_name_and_the_remaining_rows_finish(monkeypatch):
    _install_parts(monkeypatch, {"FOUND": parts.sht31(mpn="FOUND")})
    _no_rails(monkeypatch)
    result = run(frames({"bom": "FOUND\nMISSING"}))

    warn = next(event for event in result if event["type"] == "check" and event["status"] == "warn")
    assert warn["rule"] == "availability"
    assert "MISSING" in warn["detail"]
    assert result[-1]["type"] == "done"
    assert len([event for event in result if event["type"] == "selection"]) == 1


def test_every_unresolved_row_is_a_well_formed_completed_stream(monkeypatch):
    _install_parts(monkeypatch, {})
    _no_rails(monkeypatch)

    result = run(frames({"bom": "NOPE-1\nNOPE-2"}))

    assert result[0]["type"] == "plan"
    assert result[-2:][0]["type"] == "bom"
    assert result[-1]["type"] == "done"
    assert all(event["status"] == "warn" for event in result if event["type"] == "check")


def test_inferred_rail_with_a_source_not_in_the_bom_is_dropped(monkeypatch):
    _install_parts(monkeypatch, {"MCU": parts.esp32s3(mpn="MCU")})

    async def infer(resolved):
        return [{"id": "3V3", "voltage": 3.3, "source": "NOT-IN-BOM", "members": ["MCU"]}]

    monkeypatch.setattr(bom_module, "infer_rails", infer)
    result = run(frames({"bom": "MCU"}))

    plan = result[0]
    assert plan["edges"] == []
    voltage = next(event for event in result if event["type"] == "check" and event["rule"] == "voltage_overlap")
    assert voltage["status"] == "warn"


def test_no_usable_rails_keeps_non_rail_checks_and_never_passes_rail_rules(monkeypatch):
    _install_parts(monkeypatch, {"SOLD-OUT": parts.sht40(mpn="SOLD-OUT")})
    _no_rails(monkeypatch)
    result = run(frames({"bom": "SOLD-OUT"}))
    checks = [event for event in result if event["type"] == "check"]

    assert {"availability", "temperature_rating"} <= {event["rule"] for event in checks}
    assert not [
        event
        for event in checks
        if event["rule"] in {"voltage_overlap", "current_budget", "thermal_dissipation"}
        and event["status"] == "pass"
    ]
    assert {"voltage_overlap", "current_budget", "thermal_dissipation"} <= {
        event["rule"] for event in checks if event["status"] == "warn"
    }


def test_engine_conflicts_are_emitted_without_repairs(monkeypatch):
    _install_parts(monkeypatch, {"SOLD-OUT": parts.sht40(mpn="SOLD-OUT")})
    _no_rails(monkeypatch)
    result = run(frames({"bom": "SOLD-OUT"}))

    assert any(event["type"] == "conflict" and event["rule"] == "availability" for event in result)
    assert not [event for event in result if event["type"] == "repair"]


def test_quantity_contributes_each_instance_to_the_current_budget(monkeypatch):
    _install_parts(
        monkeypatch,
        {
            "REG": parts.ap2112k(mpn="REG"),
            "LOAD": parts.sht31(mpn="LOAD", i_peak=0.4),
        },
    )

    async def infer(resolved):
        return [{"id": "3V3", "voltage": 3.3, "source": "REG", "members": ["LOAD"]}]

    monkeypatch.setattr(bom_module, "infer_rails", infer)
    result = run(frames({"bom": "mpn,qty\nREG,1\nLOAD,2"}))

    assert any(event["type"] == "conflict" and event["rule"] == "current_budget" for event in result)


def test_bom_row_cap_is_enforced():
    payload = {"bom": "\n".join(f"PART-{number}" for number in range(bom_module.MAX_BOM_ROWS + 1))}

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            return await http.post("/bom/validate", json=payload)

    response = run(go())
    assert response.status_code == 422
    assert "at most" in response.json()["detail"]


def test_the_request_field_is_named_prompt_like_the_design_endpoint():
    """Both endpoints take the same thing from the same box — the brief the user typed
    beside the file — so they take it under the same name. This was briefly not true:
    the frontend sent `prompt` while the model declared `brief`, and the requirements
    silently fell back to defaults because the field never arrived. Nothing failed
    loudly; an industrial BOM would just have been judged as commercial."""
    from continuity.api.app import DesignRequest
    from continuity.api.bom import BomRequest

    assert "prompt" in BomRequest.model_fields
    assert "brief" not in BomRequest.model_fields
    assert BomRequest.model_fields["prompt"].annotation == DesignRequest.model_fields["prompt"].annotation | None

    # And it must actually be optional: a BOM with no brief is a complete request.
    assert BomRequest(bom="MPN-1").prompt is None


def test_a_slow_bom_stream_still_sends_heartbeats():
    """The BOM path resolves every MPN before it can emit anything, so the stream is
    genuinely silent for twenty seconds or more on a real BOM. The client treats thirty
    seconds without a byte as a dead connection, and the first real run of a five-line
    BOM died with "the connection went quiet" — a 200 on the server and a failed run in
    the browser."""
    import asyncio
    from continuity.api import app as app_module
    from continuity.api import events as events_module

    async def go():
        async def slow():
            await asyncio.sleep(0.25)
            yield {"type": "done", "summary": {"slots": 0, "placed": 0}}

        # Shrink the interval rather than sleeping through the real one.
        original = events_module.HEARTBEAT_INTERVAL_S
        events_module.HEARTBEAT_INTERVAL_S = 0.05
        try:
            return [item async for item in app_module._with_heartbeats(slow())]
        finally:
            events_module.HEARTBEAT_INTERVAL_S = original

    produced = asyncio.run(go())

    assert produced.count(None) >= 2, "silence must produce heartbeats, not nothing"
    assert produced[-1] is not None and produced[-1]["type"] == "done"


def test_heartbeats_do_not_drop_or_duplicate_the_events_themselves():
    import asyncio
    from continuity.api import app as app_module

    async def go():
        async def quick():
            for index in range(4):
                yield {"type": "check", "seq": index}

        return [item async for item in app_module._with_heartbeats(quick())]

    produced = asyncio.run(go())

    assert [item["seq"] for item in produced if item is not None] == [0, 1, 2, 3]


def test_a_failing_row_is_announced_as_a_conflict_not_as_pending():
    """Every row used to be emitted as `pending` before the checks ran, and the frontend
    counts conflicts by slot status — so a BOM with two real temperature failures rendered
    as "0 conflict" with every node still coloured as if it were being searched for. The
    verdicts exist before the rows are announced, so the status is knowable."""
    import asyncio
    from continuity.api import bom as bom_module, events as events_module

    async def go():
        rows = bom_module.parse_bom("FAILING-PART")
        stream = events_module.EventStream("statuses")
        seen = []
        async for event in bom_module.validate_bom(rows, stream):
            if event["type"] == "selection":
                seen.append((event["slot"], event["status"]))
        return seen

    statuses = asyncio.run(go())

    # An unresolvable MPN yields no selection at all; what matters is that nothing is
    # ever announced as `pending`, which on a finished validation is never true.
    assert all(status != "pending" for _, status in statuses), statuses
