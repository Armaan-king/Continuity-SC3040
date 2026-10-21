"""The board-independent facts a part can carry across deployments.

Distributor listings are current but incomplete. These facts preserve measurements and
properties learned on an earlier run, while the whitelist keeps a board-specific verdict
from ever being mistaken for a property of the part itself.
"""

from __future__ import annotations

import math
from typing import Any

from ..engine.models import DOSSIER_SOURCE, PartSpec

DOSSIER_FIELDS: frozenset[str] = frozenset(
    {
        "package",
        "theta_ja",
        "topology",
        "synchronous",
        "efficiency",
        "temp_min",
        "temp_max",
    }
)
"""Properties that remain true for this MPN on every board."""

DOSSIER_PROVENANCE_PREFIX = DOSSIER_SOURCE
"""One spelling, owned by the engine, because `rules` and `models` match on it too."""

_FLOAT_FIELDS = frozenset({"theta_ja", "efficiency", "temp_min", "temp_max"})

NOT_STATED = frozenset({"-", "--", "–", "—", "n/a", "na", "none", "null", "tbd", "?"})
"""Values a listing uses to mean "we did not say", which must never become a fact.

JLCPCB publishes a bare `-` for an unknown package. Nothing upstream treats that as
absent, so it reaches here as an ordinary string — and this is the one place where a
transient blank would become *durable* and then gap-fill a later run's genuinely empty
field with a dash. Refuse it at the boundary rather than teaching every reader about it.
"""


def _is_stated(value: object) -> bool:
    return not (isinstance(value, str) and value.strip().casefold() in NOT_STATED)


def facts_from_part(part: PartSpec) -> list[tuple[str, str, str, str | None]]:
    """(mpn, field, value, source) for every whitelisted field this part actually carries."""
    facts: list[tuple[str, str, str, str | None]] = []
    for field in sorted(DOSSIER_FIELDS):
        value = getattr(part, field)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if not _is_stated(value):
            continue
        source = part.theta_ja_source_line if field == "theta_ja" else part.provenance.get(field)
        source = original_source(source)
        facts.append((part.mpn, field, repr(value) if isinstance(value, float) else str(value), source))
    return facts


def value_from_text(field: str, value: str) -> Any | None:
    """Decode a stored fact only when it still has the declared `PartSpec` type."""
    if field not in DOSSIER_FIELDS or not value:
        return None
    if field in _FLOAT_FIELDS:
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    if field == "synchronous":
        return {"True": True, "False": False}.get(value)
    return value.strip() or None


def provenance(source: str | None) -> str:
    """Make a carried-forward fact visibly distinct from this run's live payload."""
    return f"{DOSSIER_PROVENANCE_PREFIX} ({source or 'source unavailable'})"


def original_source(value: str | None) -> str | None:
    """Avoid nesting the dossier label when a carried-forward fact is refreshed."""
    prefix = f"{DOSSIER_PROVENANCE_PREFIX} ("
    if value is not None and value.startswith(prefix) and value.endswith(")"):
        return value[len(prefix) : -1]
    return value
