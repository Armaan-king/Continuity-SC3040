"""User-visible history of engine findings, kept outside every decision path."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Request

from .auth import current_user, store_of
from .findings import Finding
from .store import User

router = APIRouter(tags=["memory"])

PART_LIMIT = 100

_ACCEPTED = re.compile(
    r"^Accepted on your say-so — (?P<rule>.+?) on .+ stays on the board as a warning\.$"
)
"""Matches `graph.nodes.acceptance_message`, which is the only evidence a run gives that a
user waived a finding — acceptance has no structured signal in the event contract.

`tests/test_memory.py` builds the message through that function and asserts this pattern
matches it, so rewording one side fails loudly instead of quietly recording every accepted
finding as unresolved."""


class FindingRecorder:
    """Correlate existing stream frames without contributing to a run's decisions."""

    def __init__(self) -> None:
        self._selections: dict[str, dict[str, str | None]] = {}
        self._findings: list[Finding] = []
        self._pending_repairs: dict[str, Finding] = {}
        self._failed_after_repair: set[int] = set()

    def feed(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "selection":
            self._selection(event)
        elif kind == "conflict":
            self._conflict(event)
        elif kind == "repair":
            self._repair(event)
        elif kind == "check":
            self._check(event)
        elif kind == "reasoning":
            self._acceptance(event)

    def findings(self) -> list[Finding]:
        """Only materialised findings: without an MPN there is nothing useful to remember."""
        for finding in self._findings:
            if finding.outcome == "repaired" and id(finding) not in self._failed_after_repair:
                finding.worked = True
        return list(self._findings)

    def _selection(self, event: dict[str, Any]) -> None:
        slot = event.get("slot")
        part = event.get("part")
        if not isinstance(slot, str) or not isinstance(part, dict):
            return
        mpn = part.get("mpn")
        if not isinstance(mpn, str) or not mpn:
            return
        self._selections[slot] = {
            "mpn": mpn,
            "manufacturer": _text(part.get("manufacturer")),
            "lifecycle": _text(part.get("lifecycle")),
        }
        pending = self._pending_repairs.get(slot)
        if pending is not None and pending.mpn != mpn:
            pending.outcome = "repaired"
            pending.replacement_mpn = mpn
            self._pending_repairs.pop(slot, None)

    def _conflict(self, event: dict[str, Any]) -> None:
        involved = event.get("involved")
        if not isinstance(involved, list) or not involved or not isinstance(involved[0], str):
            return
        slot = involved[0]
        selected = self._selections.get(slot)
        rule = event.get("rule")
        verdict = event.get("message")
        if selected is None or not isinstance(rule, str) or not isinstance(verdict, str):
            return
        self._findings.append(
            Finding(
                rule=rule,
                slot=slot,
                mpn=selected["mpn"] or "",
                verdict=verdict,
                manufacturer=selected["manufacturer"],
                lifecycle=selected["lifecycle"],
                signature=_text(event.get("signature")),
            )
        )
        repaired = next(
            (
                item
                for item in reversed(self._findings[:-1])
                if item.rule == rule and item.slot == slot and item.outcome == "repaired"
            ),
            None,
        )
        if repaired is not None:
            repaired.worked = False
            self._failed_after_repair.add(id(repaired))

    def _repair(self, event: dict[str, Any]) -> None:
        slot = event.get("slot")
        action = event.get("action")
        if not isinstance(slot, str) or not isinstance(action, str):
            return
        finding = next(
            (
                item
                for item in reversed(self._findings)
                if item.slot == slot and item.outcome == "unresolved"
            ),
            None,
        )
        if finding is not None:
            finding.action = action
            self._pending_repairs[slot] = finding

    def _check(self, event: dict[str, Any]) -> None:
        slot = event.get("slot")
        rule = event.get("rule")
        status = event.get("status")
        if not isinstance(slot, str) or not isinstance(rule, str) or status == "fail":
            return
        finding = next(
            (
                item
                for item in reversed(self._findings)
                if item.slot == slot and item.rule == rule and item.outcome == "repaired"
            ),
            None,
        )
        if finding is not None:
            finding.worked = True

    def _acceptance(self, event: dict[str, Any]) -> None:
        text = event.get("text")
        if not isinstance(text, str):
            return
        matched = _ACCEPTED.match(text)
        if matched is None:
            return
        rule = matched.group("rule").replace(" ", "_")
        finding = next(
            (
                item
                for item in reversed(self._findings)
                if item.rule == rule and item.outcome == "unresolved"
            ),
            None,
        )
        if finding is not None:
            finding.outcome = "accepted"
            self._pending_repairs.pop(finding.slot, None)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


@router.get("/memory")
async def memory(request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    return await store_of(request).memory_for_user(user.id, part_limit=PART_LIMIT)
