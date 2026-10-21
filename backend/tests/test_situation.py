"""Situation signatures stay categorical so old repairs cannot name a new part."""

from __future__ import annotations

from continuity.engine import rules
from continuity.engine.situation import signature
from continuity.parts import categories
from tests import parts
from tests.boards import usb_board


JLCPCB_LDO_CATEGORY = "Voltage Regulators - Linear, Low Drop Out (LDO) Regulators"
"""What a real listing states. `PartSpec.category` carries this, not our own name."""


def _thermal(board):
    return next(
        v for v in rules.failures(rules.evaluate(board)) if v.rule == "thermal_dissipation"
    )


def test_thermal_signature_describes_the_demo_shape_without_part_identity():
    board = usb_board(
        regulator=parts.ldo_600ma(category=JLCPCB_LDO_CATEGORY),
        loads={"mcu": parts.esp32s3(i_peak=0.355)},
        input_voltage=12.0,
    )

    assert signature(
        _thermal(board), board, category=categories.canonical(JLCPCB_LDO_CATEGORY)
    ) == "thermal_dissipation|regulator|linear|pkg:SOT|drop:>=8V|load:100-500mA"


def test_a_signature_never_contains_the_distributors_own_category_wording():
    """The caller resolves our name for the kind of part; a vendor's wording is not a key.

    This is the failure the first version of this test could not see: its fixture passed
    an already-canonical `category`, while the live path fed `PartSpec.category` — the
    distributor's full title — straight into the signature. A re-titled JLCPCB category
    would then have stopped every precedent matching with nothing failing anywhere.
    """
    board = usb_board(
        regulator=parts.ldo_600ma(category=JLCPCB_LDO_CATEGORY),
        loads={"mcu": parts.esp32s3(i_peak=0.355)},
        input_voltage=12.0,
    )
    conflict = _thermal(board)

    resolved = signature(conflict, board, category=categories.canonical(JLCPCB_LDO_CATEGORY))
    assert JLCPCB_LDO_CATEGORY not in resolved
    assert "|regulator|" in resolved

    # And with no category resolved, the component is omitted rather than guessed at.
    assert signature(conflict, board) == (
        "thermal_dissipation|linear|pkg:SOT|drop:>=8V|load:100-500mA"
    )


def test_canonical_maps_a_real_listing_title_to_our_own_name():
    assert categories.canonical(JLCPCB_LDO_CATEGORY) == "regulator"
    assert categories.canonical("Some Category We Never Promised") is None
    assert categories.canonical("") is None
    assert categories.canonical(None) is None


def test_canonical_accepts_a_name_that_is_already_ours():
    """The offline catalogue states our names; JLCPCB states its own. Both must resolve.

    Found by re-recording the walkthrough: its conflicts carried
    `thermal_dissipation|linear|pkg:SOT|…` with no category, because the fixture's
    `regulator` was not recognised — while a live board produced `…|regulator|linear|…`.
    Signatures are matched exactly, so the walkthrough's precedents could never fire.
    """
    assert categories.canonical("regulator") == "regulator"
    assert categories.canonical("MCU") == "mcu"
    assert categories.canonical(JLCPCB_LDO_CATEGORY) == "regulator"
    assert categories.canonical("not a category we know") is None
