"""Regression coverage for package-name reference-table lookups."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from continuity.engine import packages


@pytest.mark.parametrize(
    ("package", "expected"),
    [
        ("SOT-223", "SOT"),
        ("SOIC-8_EP", "SOIC"),
        ("VQFN-16", "QFN"),
        ("TO-220-3", "TO"),
        (None, None),
    ],
)
def test_family_normalises_known_package_spellings(package, expected):
    assert packages.family(package) == expected


@pytest.mark.parametrize(
    ("package", "expected"),
    [
        ("TO-263-5", 50.0),
        ("TO-263-3", 50.0),
        ("TSOT-23-6", 240.0),
        ("SOT-23-3L", 250.0),
        ("SOT-23-6L", 240.0),
        ("TO-252-2(DPAK)", 92.0),
        ("TO-252-2L", 92.0),
        ("TO-252-3", 92.0),
        ("TO-252-5", 92.0),
        ("SOP-8", 120.0),
        ("SOT-23-THIN-6", 240.0),
    ],
)
def test_theta_ja_folds_known_regulator_package_spellings(package, expected):
    assert packages.theta_ja(package) == expected


@pytest.mark.parametrize(
    "package", ["SOIC-8-EP", "SO-8-EP", "SOP-8-EP", "ESOP-8", "HSOP-8"]
)
def test_theta_ja_uses_the_exposed_pad_soic_value(package):
    theta = packages.theta_ja(package)

    assert theta == 65.0
    assert theta != 120.0


def test_exposed_pad_without_an_exposed_pad_entry_stays_unknown():
    assert packages.theta_ja("TSSOP-16-EP") is None


def test_vqfn_exposed_pad_stays_unknown_without_a_qfn_ep_entry():
    assert packages.theta_ja("VQFN-20-EP(3.5x3.5)") is None


@pytest.mark.parametrize("package", ["SOT-6", "VSON-14-EP(3x4)", "", None, "banana"])
def test_theta_ja_returns_none_for_deliberately_unknown_packages(package):
    assert packages.theta_ja(package) is None


def test_pin_count_folding_does_not_replace_exact_entries():
    assert packages.theta_ja("SOT-23-5") == 250.0
    assert packages.theta_ja("SOT-223") == 62.0


def test_body_mm_uses_the_same_name_fold():
    assert packages.body_mm("TO-263-5") == (10.0, 9.9)
    assert packages.body_mm("SOP-8") == (4.9, 3.9)


_REGULATOR_SUBCATEGORIES = {
    "DC-DC Converters",
    "Voltage Regulators - Linear, Low Drop Out (LDO) Regulators",
    "DC-DC Power Modules",
    "Isolated Power Modules",
    "AC-DC Controllers and Regulators",
}


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
"""Anchored to this file, not to the working directory.

`Path("fixtures")` resolved against the CWD, so running the suite from the repository
root instead of `backend/` found no files at all — and a measurement over zero rows is
not a weaker test, it is a test of nothing.
"""


def _jlc_rows():
    for path in FIXTURES.glob("jlc_search*.json"):
        payload = json.loads(path.read_text())
        response = payload.get("response", payload)
        yield from response.get("results", ())


def test_theta_ja_covers_most_fixture_regulator_packages(capsys):
    """Coverage guard against regression; the pre-fold baseline was 55%."""
    rows = [row for row in _jlc_rows() if row.get("subcategory") in _REGULATOR_SUBCATEGORIES]

    # Without this the assertion below can pass by measuring nothing.
    assert len(rows) > 100, f"expected the recorded regulator rows, found {len(rows)}"

    resolved = sum(packages.theta_ja(row.get("package")) is not None for row in rows)
    coverage = resolved / len(rows)

    print(f"theta_ja fixture coverage: {resolved}/{len(rows)} ({coverage:.1%})")
    assert coverage >= 0.80
