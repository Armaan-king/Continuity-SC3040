"""Owner-scoped report, comparison, and optimistic revision commands."""
from __future__ import annotations

import csv
import io
from dataclasses import replace
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth import current_user, store_of
from .store import User
from ..designs import service
from ..designs.guidance import review_guidance
from ..planner import topology
from ..engine.models import Port, SignalNet

router = APIRouter()


class RequirementEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    expected_revision: str = Field(min_length=1, max_length=128)
    ambient_c: int | None = Field(default=None, ge=-100, le=200)
    input_voltage: float | None = Field(default=None, gt=0, le=1000)
    current_margin: float | None = Field(default=None, ge=0, le=2)
    min_stock: int | None = Field(default=None, ge=0, le=10_000_000)
    max_lead_days: int | None = Field(default=None, ge=0, le=3650)
    priority: Literal["cost", "size", "availability"] | None = None
    temp_range: tuple[int, int] | None = None
    lifetime_hours: float | None = Field(default=None, gt=0, le=87600)
    supply_capacity_mah: float | None = Field(default=None, gt=0, le=10_000_000)
    max_package_mm: float | None = Field(default=None, gt=0, le=1000)
    input_source: str | None = None

    @model_validator(mode="after")
    def valid_range(self):
        if self.input_source is not None and self.input_source not in topology.INPUT_SOURCES:
            raise ValueError("Choose a supported input source.")
        if self.temp_range and not -273 <= self.temp_range[0] < self.temp_range[1] <= 300:
            raise ValueError("Temperature range must have an increasing minimum and maximum.")
        return self


class RestoreRequest(BaseModel):
    expected_revision: str = Field(min_length=1, max_length=128)


class NetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    protocol: Literal["I2C", "SPI", "UART", "CAN", "USB", "I2S", "1-WIRE"]
    controller: str = Field(min_length=1, max_length=80)
    peripherals: list[str] = Field(min_length=1, max_length=32)


class ConnectionsEdit(BaseModel):
    expected_revision: str
    nets: list[NetInput] = Field(default_factory=list, max_length=32)
    confirm_power: bool = False


async def owned(request: Request, revision_id: str, user: User) -> dict:
    revision = await store_of(request).revision_for_user(revision_id, user.id)
    if revision is None:
        raise HTTPException(404, "No such revision")
    return revision


@router.get("/projects/{project_id}/revisions")
async def history(project_id: str, request: Request, user: User = Depends(current_user)):
    store = store_of(request)
    if await store.project_for_user(project_id, user.id) is None:
        raise HTTPException(404, "No such project")
    return {"revisions": await store.revisions_for_project(project_id, user.id)}


@router.get("/revisions/{revision_id}")
async def read(revision_id: str, request: Request, user: User = Depends(current_user)):
    revision = await owned(request, revision_id, user)
    # Presentation guidance is derived separately; immutable documents/hashes stay intact.
    return {**revision, "guidance": review_guidance(revision["document"])}


@router.get("/revisions/{revision_id}/compare/{other_id}")
async def compare(revision_id: str, other_id: str, request: Request, user: User = Depends(current_user)):
    before, after = await owned(request, revision_id, user), await owned(request, other_id, user)
    if before["project_id"] != after["project_id"]:
        raise HTTPException(422, "Compare revisions from the same project")
    return service.compare(before["document"], after["document"])


async def save(request: Request, user: User, revision: dict, document: dict, reason: str, expected: str):
    try:
        revision_id = await store_of(request).save_revision(revision["thread_id"], user.id, document, reason, expected)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"revision_id": revision_id, "project_id": revision["project_id"]}


@router.post("/revisions/{revision_id}/requirements")
async def edit_requirements(revision_id: str, body: RequirementEdit, request: Request, user: User = Depends(current_user)):
    revision = await owned(request, revision_id, user)
    if revision_id != body.expected_revision:
        raise HTTPException(409, "Reload the current revision before editing requirements")
    original = revision["document"]
    board = service.board_from_document(original)
    changes = body.model_dump(exclude_unset=True, exclude={"expected_revision"})
    # Optional constraints can be cleared; mandatory inputs cannot be made null.
    optional = {"lifetime_hours", "supply_capacity_mah", "max_package_mm", "min_stock"}
    if not changes or any(value is None and name not in optional for name, value in changes.items()):
        raise HTTPException(422, "Supply at least one valid requirement")
    requirements = replace(board.requirements, **changes)
    rails = dict(board.rails)
    if {"input_voltage", "input_source"} & changes.keys():
        inputs = [rail for rail in rails.values() if rail.source is None]
        if len(inputs) > 1:
            raise HTTPException(422, "This design has multiple external rails; revise the circuit inputs individually.")
        if "input_source" in changes and "input_voltage" not in changes:
            requirements = replace(requirements, input_voltage=None)
            changes["input_voltage"] = None
        supply = topology.power_source(requirements)
        for key, rail in rails.items():
            if rail.source is None:
                rails[key] = replace(rail, voltage=supply.voltage, i_limit=supply.i_limit if "input_source" in changes else rail.i_limit,
                                     basis="User-confirmed input selection; nominal supply model")
    board = replace(board, requirements=requirements, rails=rails)
    confirmed = list({row["field"] for row in original["requirements"] if row["basis"] == "confirmed"} | changes.keys())
    document = service.document(board, prompt=original["prompt"], origin=original["origin"],
        quantities=original["quantities"], confirmed=confirmed,
        decisions=[*original.get("decisions", []), {"action": "edit_requirements", "changes": changes}])
    return await save(request, user, revision, document, "Requirements updated and revalidated", body.expected_revision)


@router.post("/revisions/{revision_id}/restore")
async def restore(revision_id: str, body: RestoreRequest, request: Request, user: User = Depends(current_user)):
    revision = await owned(request, revision_id, user)
    original = revision["document"]
    document = service.document(service.board_from_document(original), prompt=original["prompt"],
        origin=original["origin"], quantities=original["quantities"],
        confirmed=[row["field"] for row in original["requirements"] if row["basis"] == "confirmed"],
        decisions=[*original.get("decisions", []), {"action": "restore", "source_revision": revision_id}])
    return await save(request, user, revision, document, "Earlier revision restored and revalidated", body.expected_revision)


@router.post("/revisions/{revision_id}/connections")
async def edit_connections(revision_id: str, body: ConnectionsEdit, request: Request, user: User = Depends(current_user)):
    revision = await owned(request, revision_id, user)
    if revision_id != body.expected_revision:
        raise HTTPException(409, "Reload the current revision before editing connections")
    original = revision["document"]
    board = service.board_from_document(original)
    if len({net.id for net in body.nets}) != len(body.nets):
        raise HTTPException(422, "Connection names must be unique")
    nets = []
    for net in body.nets:
        members = [net.controller, *net.peripherals]
        if len(set(members)) != len(members) or any(key not in board.slots for key in members):
            raise HTTPException(422, "Each connection must reference distinct components on this revision")
        nets.append(SignalNet(net.id, net.protocol, (Port(net.controller, net.protocol, "controller"),
            *(Port(key, net.protocol, "peripheral") for key in net.peripherals)), "confirmed"))
    rails = {key: replace(rail, basis="User-confirmed power connection") if body.confirm_power else rail for key, rail in board.rails.items()}
    board = replace(board, signal_nets=tuple(nets), rails=rails)
    document = service.document(board, prompt=original["prompt"], origin=original["origin"], quantities=original["quantities"],
        confirmed=[row["field"] for row in original["requirements"] if row["basis"] == "confirmed"],
        decisions=[*original.get("decisions", []), {"action": "confirm_connections", "power": body.confirm_power}])
    return await save(request, user, revision, document, "Connections updated and revalidated", body.expected_revision)


@router.get("/revisions/{revision_id}/report.json")
async def report(revision_id: str, request: Request, user: User = Depends(current_user)):
    revision = await owned(request, revision_id, user)
    return JSONResponse({"revision_id": revision_id, "content_hash": revision["content_hash"], **revision["document"]},
        headers={"Content-Disposition": f'attachment; filename="continuity-{revision_id}-report.json"'})


@router.get("/revisions/{revision_id}/bom.csv")
async def export(revision_id: str, request: Request, user: User = Depends(current_user)):
    revision = await owned(request, revision_id, user)
    buffer = io.StringIO()
    rows = revision["document"]["bom"]["rows"]
    writer = csv.DictWriter(buffer, fieldnames=["revision_id", "slot", "mpn", "manufacturer", "qty", "unit_price", "currency", "stock", "product_url"], extrasaction="ignore")
    writer.writeheader()
    writer.writerows({**row, "revision_id": revision_id} for row in rows)
    return PlainTextResponse(buffer.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="continuity-{revision_id}.csv"'})
