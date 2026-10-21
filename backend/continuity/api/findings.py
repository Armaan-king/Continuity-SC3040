"""Shared finding value object for API recording and storage."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Finding:
    rule: str
    slot: str
    mpn: str
    verdict: str
    outcome: str = "unresolved"
    action: str | None = None
    replacement_mpn: str | None = None
    manufacturer: str | None = None
    lifecycle: str | None = None
    signature: str | None = None
    worked: bool = False
