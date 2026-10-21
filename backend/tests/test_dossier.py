"""Board-independent part facts are filtered before they can become durable memory."""

from __future__ import annotations

from continuity.engine.models import PartSpec
from continuity.parts.dossier import DOSSIER_FIELDS, facts_from_part


def test_facts_from_part_keeps_only_known_nonempty_part_properties():
    part = PartSpec(
        mpn="TPS54331DR",
        manufacturer="TI",
        description="buck regulator",
        category="DC-DC",
        package="SOIC-8",
        theta_ja=62.0,
        theta_ja_source_line="RθJA Junction-to-ambient thermal resistance 62.0",
        topology="buck",
        synchronous=False,
        efficiency=0.91,
        temp_min=None,
        temp_max=125.0,
        provenance={"topology": "Topology", "efficiency": "Efficiency"},
    )

    assert DOSSIER_FIELDS == frozenset(
        {"package", "theta_ja", "topology", "synchronous", "efficiency", "temp_min", "temp_max"}
    )
    assert facts_from_part(part) == [
        ("TPS54331DR", "efficiency", "0.91", "Efficiency"),
        ("TPS54331DR", "package", "SOIC-8", None),
        ("TPS54331DR", "synchronous", "False", None),
        ("TPS54331DR", "temp_max", "125.0", None),
        ("TPS54331DR", "theta_ja", "62.0", "RθJA Junction-to-ambient thermal resistance 62.0"),
        ("TPS54331DR", "topology", "buck", "Topology"),
    ]


def test_facts_from_part_never_emits_empty_values():
    part = PartSpec(
        mpn="EMPTY",
        manufacturer="",
        description="",
        category="",
        package="",
        topology="",
    )

    assert facts_from_part(part) == []


def test_a_listings_not_stated_placeholder_never_becomes_a_durable_fact():
    """JLCPCB publishes a bare "-" for an unknown package.

    Found live: `HS91L02W2C01` was stored as `package = "-"`. Nothing upstream treats a
    dash as absent, and the dossier is the one place a transient blank becomes permanent
    and can later gap-fill a genuinely empty field on another board.
    """
    from continuity.engine.models import PartSpec
    from continuity.parts.dossier import facts_from_part

    part = PartSpec(
        mpn="HS91L02W2C01",
        manufacturer="m",
        description="d",
        category="Optoelectronics",
        package="-",
    )
    assert [field for _, field, _, _ in facts_from_part(part)] == []

    stated = PartSpec(
        mpn="HS91L02W2C01",
        manufacturer="m",
        description="d",
        category="Optoelectronics",
        package="SOP-8",
    )
    assert ("HS91L02W2C01", "package", "SOP-8", None) in facts_from_part(stated)
