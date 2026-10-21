"""Datasheet-only θJA extraction stays evidence-bound and offline."""

from __future__ import annotations

import asyncio

import pytest

from continuity.parts import datasheet


TPS54331_THERMAL = """6.4 Thermal Information
THERMAL METRIC(1) D DDA
UNIT
8 PINS 8 PINS
RθJA Junction-to-ambient thermal resistance 116.3 48.7
°C/W
RθJC(top) Junction-to-case (top) thermal resistance 53.7 52.4
RθJB Junction-to-board thermal resistance 57.1 25.5
ψJT Junction-to-top characterization parameter 12.9 8.4
ψJB Junction-to-board characterization parameter 56.5 25.2
RθJC(bot) Junction-to-case (bottom) thermal resistance — 2.3"""

THETA_LINE = "RθJA Junction-to-ambient thermal resistance 116.3 48.7"
JC_LINE = "RθJC(top) Junction-to-case (top) thermal resistance 53.7 52.4"


def _extract(monkeypatch, reply):
    monkeypatch.setattr(datasheet.llm, "available", lambda: True)

    async def complete_json(*_args):
        return reply

    monkeypatch.setattr(datasheet.llm, "complete_json", complete_json)
    return asyncio.run(
        datasheet.theta_ja_from_text(
            TPS54331_THERMAL, mpn="TPS54331DR", package="SOIC-8"
        )
    )


def test_extracts_plain_soic_column_and_keeps_its_real_source_line(monkeypatch, tmp_path):
    monkeypatch.setattr(datasheet, "CACHE_DIR", tmp_path)

    fact = _extract(
        monkeypatch,
        {"theta_ja": 116.3, "source_line": THETA_LINE},
    )

    assert fact == datasheet.ThermalFact(116.3, THETA_LINE, "SOIC-8")


def test_wrong_column_remains_a_reviewable_model_error(monkeypatch, tmp_path):
    """Validation proves the row was read, not that 48.7 was the D rather than DDA column."""
    monkeypatch.setattr(datasheet, "CACHE_DIR", tmp_path)

    fact = _extract(
        monkeypatch,
        {"theta_ja": 48.7, "source_line": THETA_LINE},
    )

    assert fact == datasheet.ThermalFact(48.7, THETA_LINE, "SOIC-8")


def test_invented_source_line_is_dropped(monkeypatch, tmp_path):
    monkeypatch.setattr(datasheet, "CACHE_DIR", tmp_path)

    assert _extract(monkeypatch, {"theta_ja": 116.3, "source_line": "invented row"}) is None


@pytest.mark.parametrize("value", (0, -5, 4.9, 501, "116.3", True))
def test_out_of_band_or_mistyped_theta_ja_is_dropped(monkeypatch, tmp_path, value):
    monkeypatch.setattr(datasheet, "CACHE_DIR", tmp_path)

    assert _extract(monkeypatch, {"theta_ja": value, "source_line": THETA_LINE}) is None


def test_junction_to_case_row_is_dropped_even_when_its_value_is_in_range(monkeypatch, tmp_path):
    """RθJC is a dangerous distractor: 53.7 is plausible, so validation checks the metric too."""
    monkeypatch.setattr(datasheet, "CACHE_DIR", tmp_path)

    assert _extract(monkeypatch, {"theta_ja": 53.7, "source_line": JC_LINE}) is None


@pytest.mark.parametrize("body", (b"<html>blocked</html>", b"", b"%PDF-1.7\ntruncated"))
def test_text_from_pdf_returns_none_for_unparseable_content(body):
    assert datasheet.text_from_pdf(body) is None


@pytest.mark.parametrize("url", ("http://example.com/a.pdf", "file:///tmp/a.pdf", "https:///a.pdf"))
def test_fetch_rejects_unsafe_or_hostless_urls_without_a_request(monkeypatch, url):
    class UnexpectedClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("fetch attempted a request")

    monkeypatch.setattr(datasheet.httpx, "AsyncClient", UnexpectedClient)

    assert asyncio.run(datasheet.fetch(url)) is None


def test_without_an_llm_extraction_degrades_to_none(monkeypatch, tmp_path):
    monkeypatch.setattr(datasheet, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(datasheet.llm, "available", lambda: False)

    assert asyncio.run(
        datasheet.theta_ja_from_text(TPS54331_THERMAL, mpn="TPS54331DR", package="SOIC-8")
    ) is None


def test_cache_round_trips_and_prompt_change_invalidates_it(monkeypatch, tmp_path):
    monkeypatch.setattr(datasheet, "CACHE_DIR", tmp_path)
    fact = datasheet.ThermalFact(116.3, THETA_LINE, "SOIC-8")

    datasheet._save("TPS54331DR", fact)
    assert datasheet._load("TPS54331DR") == fact

    monkeypatch.setattr(datasheet, "SYSTEM", datasheet.SYSTEM + "\nchanged")
    assert datasheet._load("TPS54331DR") is None


# ── notation and depth, measured against real vendor datasheets 11 Aug ─────────


@pytest.mark.parametrize(
    "line",
    [
        "RθJA Junction-to-ambient thermal resistance 116.3 48.7",
        "θJA Thermal resistance 45.2 °C/W",
        "Theta-JA junction to ambient 60",
        "Junction-to-ambient thermal resistance 88 °C/W",
    ],
)
def test_theta_ja_is_recognised_in_every_vendor_notation(line):
    """`RθJA` is TI house style; keying on it alone drops every other vendor."""
    assert datasheet._is_theta_ja_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "RθJC(top) Junction-to-case (top) thermal resistance 53.7 52.4",
        "RθJB Junction-to-board thermal resistance 57.1 25.5",
        "ψJT Junction-to-top characterization parameter 12.9 8.4",
        "ψJB Junction-to-board characterization parameter 56.5 25.2",
    ],
)
def test_the_neighbouring_thermal_metrics_are_never_accepted(line):
    """These sit directly beneath θJA and are a fraction of it.

    Junction-to-case on the TPS54331 is 53.7 against θJA's 116.3, so mistaking one
    understates the temperature rise — the direction that passes a board which cooks.
    """
    assert not datasheet._is_theta_ja_line(line)


def test_the_prompt_window_keeps_the_thermal_region_of_a_long_document():
    filler = "application note prose. " * 2000
    text = filler + "\nRθJA Junction-to-ambient thermal resistance 116.3 48.7\n" + filler
    window = datasheet.thermal_window(text)

    assert len(window) <= datasheet.MAX_PROMPT_CHARS
    assert "RθJA Junction-to-ambient thermal resistance 116.3 48.7" in window


def test_a_short_document_is_passed_through_whole():
    text = "RθJA Junction-to-ambient thermal resistance 116.3 48.7"
    assert datasheet.thermal_window(text) == text


def test_a_long_document_with_no_thermal_keyword_is_truncated_not_dropped():
    text = "prose. " * 5000
    assert len(datasheet.thermal_window(text)) == datasheet.MAX_PROMPT_CHARS


def test_fetch_follows_redirects_within_a_bound(monkeypatch):
    """Espressif answers 301 on its own datasheet URL and 200 one hop later.

    Measured 11 Aug. Refusing to follow turned a working vendor into a silent `None`,
    and the PDF magic-number check is what actually guarantees the body is a document.
    """
    captured = {}

    class _Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def stream(self, *_args, **_kwargs):
            raise AssertionError("not reached in this test")

    monkeypatch.setattr(datasheet.httpx, "AsyncClient", _Client)
    asyncio.run(datasheet.fetch("https://www.example.com/ds.pdf"))

    assert captured["follow_redirects"] is True
    assert captured["max_redirects"] == datasheet.MAX_REDIRECTS
