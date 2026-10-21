"""Engine-owned switching-efficiency bounds."""

from __future__ import annotations

import pytest

from continuity.engine import efficiency, regulation
from tests import parts


def switcher(topology: str | None, synchronous: bool | None):
    return parts.buck_3v3(
        topology=topology,
        synchronous=synchronous,
        efficiency=None,
        category="DC-DC Converters",
    )


@pytest.mark.parametrize(
    ("part", "expected", "label"),
    (
        (switcher("buck", True), (0.85, 0.95), "synchronous buck"),
        (switcher("buck", False), (0.75, 0.88), "non-synchronous buck"),
        (switcher("buck", None), (0.75, 0.95), "buck, rectifier type not stated"),
        (switcher("Boost、Buck", None), (0.72, 0.95), "boost and buck, rectifier type not stated"),
        (switcher("SEPIC", False), (0.68, 0.85), "sepic"),
        (switcher("flyback", None), (0.70, 0.95), "switching topology not stated"),
    ),
)
def test_efficiency_band_resolution(part, expected, label):
    assert efficiency.band_for(part) == expected
    assert efficiency.band_label(part) == label


def test_a_linear_regulator_has_no_efficiency_band():
    assert efficiency.band_for(parts.ap2112k()) is None


# ── a topology field stating more than one value ──────────────────────────────


def test_agreeing_multi_valued_topologies_are_still_a_switcher():
    """JLCPCB publishes `Topology: "Boost、Buck"` on six regulator rows in fixtures.

    Both values are switching, so the part's regulation is not in doubt. Answering
    `None` here made the band widening unreachable, because `_dissipation` refuses a
    part whose regulation is unknown before any band is ever read.
    """
    assert regulation.regulation_from_topology("Boost、Buck") == "switching"
    assert regulation.regulation_from_topology("buck,boost") == "switching"


def test_disagreeing_multi_valued_topologies_stay_unknown():
    assert regulation.regulation_from_topology("buck、ldo") is None


def test_one_unrecognised_value_makes_the_whole_field_unknown():
    """Reading a stated fact partially is how a wrong verdict starts."""
    assert regulation.regulation_from_topology("buck、flyback") is None
    assert regulation.regulation_from_topology("") is None


def test_an_agreeing_multi_topology_part_gets_the_union_of_its_bands():
    part = switcher("Boost、Buck", True)
    assert part.is_switching, "the union path is unreachable unless regulation resolves"
    assert efficiency.band_for(part) == (0.82, 0.95)
