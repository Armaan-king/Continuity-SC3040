"""Approximate switching-efficiency bands owned by the constraint engine.

They are coarse bounds from a regulator's stated topology and rectifier type, not
figures published for the individual IC. Every verdict using one identifies the band
and its approximate source in its evidence.
"""

from __future__ import annotations

from .models import PartSpec

BAND_SOURCE = "Continuity efficiency band (topology and rectifier type, approximate)"

_BANDS: dict[tuple[str, bool | None], tuple[float, float]] = {
    ("buck", True): (0.85, 0.95),
    ("buck", False): (0.75, 0.88),
    ("boost", True): (0.82, 0.93),
    ("boost", False): (0.72, 0.87),
    ("buck-boost", True): (0.78, 0.90),
    ("buck-boost", False): (0.70, 0.85),
    ("sepic", None): (0.68, 0.85),
}

_WIDEST_SWITCHING_BAND = (
    # The stipulated 70–95% unknown-topology fallback is the union of the ordinary
    # buck/boost families; a stated SEPIC resolves to its own narrower class instead.
    min(low for (topology, _), (low, _) in _BANDS.items() if topology != "sepic"),
    max(high for _, high in _BANDS.values()),
)


def _topologies(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(item.strip().casefold() for item in value.replace("、", ",").split(",") if item.strip())


def _topology_band(topology: str, synchronous: bool | None) -> tuple[float, float] | None:
    if topology == "sepic":
        return _BANDS[(topology, None)]
    if synchronous is None:
        rows = (_BANDS.get((topology, True)), _BANDS.get((topology, False)))
        known = tuple(row for row in rows if row is not None)
        if known:
            return min(low for low, _ in known), max(high for _, high in known)
        return None
    return _BANDS.get((topology, synchronous))


def band_for(part: PartSpec) -> tuple[float, float] | None:
    """Return the efficiency interval supported by a switching part's stated facts."""
    if not part.is_switching:
        return None
    bands = tuple(
        band for topology in _topologies(part.topology)
        if (band := _topology_band(topology, part.synchronous)) is not None
    )
    if not bands:
        return _WIDEST_SWITCHING_BAND
    return min(low for low, _ in bands), max(high for _, high in bands)


def band_label(part: PartSpec) -> str:
    """A short description of the categorical facts behind :func:`band_for`."""
    names = tuple(name for name in _topologies(part.topology) if _topology_band(name, part.synchronous))
    if not names:
        return "switching topology not stated"
    topology = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
    if names == ("sepic",):
        return topology
    if part.synchronous is True:
        return f"synchronous {topology}"
    if part.synchronous is False:
        return f"non-synchronous {topology}"
    return f"{topology}, rectifier type not stated"
