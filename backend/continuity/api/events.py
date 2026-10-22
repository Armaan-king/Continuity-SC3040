"""The wire format. `docs/specs/2026-08-02-contract.md` is authoritative for this file.

Every frame the frontend receives is built here, so that the contract has exactly one
implementation on the backend rather than one per node.

Two invariants the frontend depends on and will not tell you it is depending on:

- **`seq` is monotonic from 0, per thread.** The client drops anything out of order,
  and it initialises its high-water mark to -1 precisely so that `seq: 0` survives.
- **`edges` on `selection` and `conflict` are patches, merged by `id`.** Omitted fields
  keep their previous value. Sending a full replacement array works by accident until
  a field is left out.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable

from ..engine.models import Alternative, Edge, PartSpec, Slot, Verdict

HEARTBEAT = ": heartbeat\n\n"
"""Comment frame. Keeps proxies from closing an idle stream; the client ignores it."""

HEARTBEAT_INTERVAL_S = 15.0
CLIENT_TIMEOUT_S = 30.0
"""What the client treats as dead. Twice the heartbeat, so one dropped frame is survivable."""


def frame(event: dict[str, Any]) -> str:
    """One SSE frame: `data: {json}\\n\\n`. Compact, because these get chatty."""
    return f"data: {json.dumps(event, separators=(',', ':'), default=_encode)}\n\n"


def _encode(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON-serialisable")


class EventStream:
    """Builds contract-shaped events for one thread, owning its `seq` counter.

    The counter lives here rather than in graph state because a node that retries —
    and LangGraph re-executes an interrupt node when it resumes — must not reuse a
    sequence number it has already sent.

    ## Why the counter is an int and not an `itertools.count`

    A count cannot be read without advancing it, and the number has to be *readable* for
    two reasons that only appear once threads outlive the process:

    - It must be persisted when a stream ends, because that is the moment a resume
      becomes possible.
    - It must be restored when one starts, because the client drops every frame at or
      below its high-water mark. A restarted counter therefore does not error, warn, or
      render — the resumed run is discarded in silence and looks like a hang.

    `-1` means nothing has been sent, which is deliberately the same value the client
    initialises its own high-water mark to, so that `seq: 0` survives.
    """

    def __init__(self, thread_id: str, *, last_seq: int = -1) -> None:
        self.thread_id = thread_id
        self.last_seq = last_seq

    def _event(self, type_: str, **fields: Any) -> dict[str, Any]:
        self.last_seq += 1
        return {"type": type_, "seq": self.last_seq, "thread_id": self.thread_id, **fields}

    # ── lifecycle ────────────────────────────────────────────────────────────

    def plan(
        self,
        slots: Iterable[Slot],
        edges: Iterable[Edge],
        supply: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """`supply` is a *node* and deliberately not a member of `slots`.

        It is what power edges out of the board input point back to. Putting it in
        `slots` would make it something the run has to resolve a part for, which it is
        not — see `topology.SUPPLY_NODE_ID`. Clients that predate the field draw the
        board exactly as they did before, minus those edges' left-hand end.
        """
        event = self._event(
            "plan",
            slots=[_slot(s) for s in slots],
            edges=[_edge(e) for e in edges],
        )
        if supply is not None:
            event["supply"] = supply
        return event

    def slot_added(self, slot: Slot, edges: Iterable[Edge]) -> dict[str, Any]:
        """One slot declared after planning, appended rather than replacing the board."""
        return self._event("slot_added", slot=_slot(slot), edges=[_edge(e) for e in edges])

    def reasoning(self, slot: str | None, text: str, *, level: str | None = None) -> dict[str, Any]:
        return self._event("reasoning", slot=slot, text=text, **({"level": level} if level else {}))

    def candidate(self, slot: str, part: PartSpec) -> dict[str, Any]:
        return self._event("candidate", slot=slot, part=_part(part))

    def check(self, verdict: Verdict) -> dict[str, Any]:
        """One rule result. Keyed by (rule, slot, scope) — see the contract on `scope`."""
        return self._event(
            "check",
            slot=verdict.subject,
            rule=verdict.rule,
            scope=verdict.scope,
            status=verdict.status,
            detail=verdict.detail,
            unchecked=verdict.unchecked,
            original_status=verdict.original_status,
            accepted=verdict.accepted,
            evidence=[asdict(e) for e in verdict.evidence],
        )

    def conflict(
        self, verdict: Verdict, edge: str | None = None, signature: str | None = None
    ) -> dict[str, Any]:
        """`involved` is everyone participating, not everyone at fault.

        The slot at fault is the one that appears in the following `repair`. Latching
        every entry here as broken is a bug the frontend has already had once.
        """
        event = self._event(
            "conflict",
            rule=verdict.rule,
            subject=verdict.subject,
            involved=list(verdict.involved),
            edge=edge,
            message=verdict.detail,
            evidence=[
                {"slot": e.slot, "field": e.field, "value": e.value, "source": e.source}
                for e in verdict.evidence
            ],
        )
        if signature:
            event["signature"] = signature
        return event

    def repair(
        self,
        slot: str,
        action: str,
        rationale: str,
        constraint: dict[str, Any] | None = None,
        alternatives: Iterable[Alternative] = (),
    ) -> dict[str, Any]:
        return self._event(
            "repair",
            slot=slot,
            action=action,
            rationale=rationale,
            constraint=constraint or {},
            alternatives=[_alternative(a) for a in alternatives],
        )

    def selection(
        self, slot: str, part: PartSpec, status: str = "pass", edges: Iterable[Edge] = ()
    ) -> dict[str, Any]:
        event = self._event("selection", slot=slot, part=_part(part), status=status)
        patch = [_edge_patch(e) for e in edges]
        if patch:
            event["edges"] = patch
        return event

    def question(
        self, question_id: str, text: str, suggestions: Iterable[str] = ()
    ) -> dict[str, Any]:
        return self._event(
            "question", question_id=question_id, text=text, suggestions=list(suggestions)
        )

    def bom(self, rows: list[dict[str, Any]], total: float, currency: str = "USD") -> dict[str, Any]:
        return self._event("bom", rows=rows, total=round(total, 2), currency=currency)

    def decision(self, verdict: Verdict, reason: str) -> dict[str, Any]:
        return self._event("decision", action="accept_finding", rule=verdict.rule,
                           subject=verdict.subject, scope=verdict.scope, reason=reason)

    def board(self, slots: Iterable[Slot], edges: Iterable[Edge], verdicts: Iterable[Verdict],
              supply: dict[str, Any] | None = None) -> dict[str, Any]:
        """Authoritative final state, shared by the live view and saved-board restore."""
        failures = [v for v in verdicts if v.status == "fail"]
        failing = {v.subject for v in failures}
        return self._event(
            "board", slots=[{**board_slot(s), "status": (
                "pending" if s.part is None else "conflict" if s.id in failing else "pass"
            )} for s in slots], edges=[_edge(e) for e in edges], supply=supply,
            conflicts=[{
                "rule": v.rule, "involved": list(v.involved), "edge": v.scope,
                "message": v.detail, "subject": v.subject,
                "evidence": [asdict(e) for e in v.evidence],
            } for v in failures],
        )

    def done(
        self, slots: int, conflicts_resolved: int, elapsed_s: float, placed: int | None = None,
        *, verdicts: Iterable[Verdict] | None = None, stopped: bool = False,
    ) -> dict[str, Any]:
        """`slots` is what was *planned*; `placed` is what actually reached the BOM.

        These were once the same number, computed from the BOM rows — so the summary was
        derived from the survivors and could never disagree with itself. A board that
        lost a slot to an empty search reported complete.
        """
        event = self._event(
            "done",
            summary={
                "slots": slots,
                "placed": slots if placed is None else placed,
                "conflicts_resolved": conflicts_resolved,
                "elapsed_s": round(elapsed_s, 1),
            },
        )
        if verdicts is not None:
            results = list(verdicts)
            event["summary"].update(
                failures=sum(v.status == "fail" for v in results),
                warnings=sum(v.status == "warn" and not v.unchecked for v in results),
                unchecked=sum(v.unchecked for v in results),
                stopped=stopped,
            )
        return event

    def error(self, message: str, recoverable: bool = True) -> dict[str, Any]:
        return self._event("error", message=message, recoverable=recoverable)


# ── serialisation ─────────────────────────────────────────────────────────────


def _slot(slot: Slot) -> dict[str, Any]:
    return {"id": slot.id, "label": slot.label, "tier": slot.tier, "pinned": slot.pinned}


def board_slot(slot: Slot) -> dict[str, Any]:
    return {**_slot(slot), "status": slot.status,
            "part": _part(slot.part) if slot.part else None,
            "constraint": dict(slot.constraint) if slot.constraint is not None else None,
            "repair_count": slot.repair_count}


def _edge(edge: Edge) -> dict[str, Any]:
    return {
        "id": edge.id,
        "from": edge.source,
        "to": edge.target,
        "label": edge.label,
        "kind": edge.kind,
        "status": edge.status,
    }


def _edge_patch(edge: Edge) -> dict[str, Any]:
    """Only what changed. The client merges by id and keeps everything else."""
    return {"id": edge.id, "from": edge.source, "label": edge.label, "status": edge.status}


def _part(part: PartSpec) -> dict[str, Any]:
    """`PartSpec` as the contract declares it — `raw` and `provenance` stay server-side.

    They are large, and the evidence rows already carry the parts of them that a person
    needs to see. Shipping the whole payload would make every `candidate` frame heavy
    for no gain on screen.
    """
    return {
        "mpn": part.mpn,
        "manufacturer": part.manufacturer,
        "description": part.description,
        "category": part.category,
        "vmin": part.vmin,
        "vmax": part.vmax,
        "vout": part.vout,
        "i_typ": part.i_typ,
        "i_peak": part.i_peak,
        "i_max": part.i_max,
        "interfaces": list(part.interfaces),
        "role": part.role,
        "pins_required": part.pins_required,
        "pins_available": part.pins_available,
        "package": part.package,
        "theta_ja": part.theta_ja,
        "topology": part.topology,
        "efficiency": part.efficiency,
        "temp_min": part.temp_min,
        "temp_max": part.temp_max,
        "unit_price": part.unit_price,
        "currency": part.currency,
        "stock": part.stock,
        "distributor": part.distributor,
        "lifecycle": part.lifecycle,
        "lead_time_days": part.lead_time_days,
        "datasheet": part.datasheet,
        "product_url": part.product_url,
    }


def _alternative(alt: Alternative) -> dict[str, Any]:
    return {
        "mpn": alt.mpn,
        "manufacturer": alt.manufacturer,
        "unit_price": alt.unit_price,
        "currency": alt.currency,
        "stock": alt.stock,
        "lead_time_days": alt.lead_time_days,
        "reason": alt.reason,
        "recommended": alt.recommended,
        "datasheet": alt.datasheet,
    }


def bom_row(slot: str, part: PartSpec, qty: int = 1) -> dict[str, Any]:
    return {
        "slot": slot,
        "mpn": part.mpn,
        "manufacturer": part.manufacturer,
        "description": part.description,
        "qty": qty,
        "unit_price": part.unit_price,
        "currency": part.currency,
        "stock": part.stock,
        "distributor": part.distributor,
        "lead_time_days": part.lead_time_days,
        "datasheet": part.datasheet,
        "product_url": part.product_url,
    }
