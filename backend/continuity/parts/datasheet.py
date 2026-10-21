"""Read a cited θJA measurement from a regulator datasheet.

Datasheet hosts are unreliable, and a package's θJA is not safely knowable from its
name.  This module therefore treats both downloading and extraction as best-effort
operations that return a fact only when its quoted source text can be checked.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx
from pypdf import PdfReader

from .. import env, llm

log = logging.getLogger(__name__)

CACHE_DIR = env.cache_dir("datasheets")
MAX_PDF_BYTES = 12 * 1024 * 1024
FETCH_TIMEOUT_S = 10.0
MAX_REDIRECTS = 5

MAX_PDF_PAGES = 40
"""How deep to read before giving up on finding a thermal table.

TI puts *Thermal Information* in section 6.4 — page 5 of the TPS54331 — and 12 pages was
chosen from that one example. Measured 11 Aug against other vendors, 12 is too shallow:
neither NXP's UJA1075A nor Espressif's ESP32-WROOM states a thermal resistance anywhere in
its first 12 pages. Datasheet section order is a house style, not a standard.

Reading deeper costs nothing but local CPU. What it must not do is put forty pages of
application notes in front of the model, which is what `thermal_window` is for.
"""

MAX_PROMPT_CHARS = 6000
"""Above this, only the region around a thermal keyword is sent."""


@dataclass(frozen=True)
class ThermalFact:
    """A θJA figure and the datasheet text that supports it."""

    theta_ja: float
    source_line: str
    package_column: str


SYSTEM = """You extract one thermal measurement from supplied datasheet text.

Return ONE JSON object with ONLY these keys: theta_ja, source_line.

Rules:
- theta_ja is junction-to-ambient thermal resistance RθJA, in °C/W, as a number with
  no unit suffix. source_line is the exact full datasheet line from which it was read.
- The part MPN and package are supplied. A thermal table may have several package
  columns, sometimes with the same pin count. Select only the column that matches the
  supplied package. Return null for both fields if the matching column is unclear.
- RθJC, RθJB, ψJT and ψJB are NOT θJA and must never be returned.
- MEASUREMENTS must be READ from the datasheet text. Use null when it does not state
  one. Never guess, never infer a typical value, never copy a number from a similar
  part you know. This applies without exception.

Return the JSON object and nothing else."""


def text_from_pdf(data: bytes) -> str | None:
    """Extract at most the opening datasheet pages, or return no text on any failure."""
    if not data or len(data) > MAX_PDF_BYTES or not data.startswith(b"%PDF-"):
        return None
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages[:MAX_PDF_PAGES])
    except Exception as error:  # malformed PDFs are an ordinary missing fact
        log.debug("could not read PDF text: %s", error)
        return None


def _prompt(text: str, mpn: str, package: str) -> str:
    return json.dumps(
        {"mpn": mpn, "package": package, "datasheet_text": text},
        ensure_ascii=False,
        indent=1,
    )


def _collapsed(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


_THETA_JA_NAMES = re.compile(
    r"\bR?\s*(?:θ|Θ|THETA)\s*[-_]?\s*JA\b|junction[\s\-–]*to[\s\-–]*ambient", re.IGNORECASE
)

_OTHER_METRICS = re.compile(
    r"junction[\s\-–]*to[\s\-–]*(?:case|board|top)|\bR?\s*(?:θ|Θ|THETA)\s*[-_]?\s*J[CB]\b|[ψΨ]\s*J[TB]\b",
    re.IGNORECASE,
)


def thermal_window(text: str) -> str:
    """The region of a datasheet worth putting in front of the model.

    A forty-page document is mostly application notes, and burying one table in them
    costs tokens and dilutes attention. When the text is long, keep only what surrounds a
    thermal keyword.
    """
    if len(text) <= MAX_PROMPT_CHARS:
        return text
    hits = [match.start() for match in _THETA_JA_NAMES.finditer(text)]
    if not hits:
        return text[:MAX_PROMPT_CHARS]
    half = MAX_PROMPT_CHARS // 2
    start = max(0, hits[0] - half)
    return text[start : start + MAX_PROMPT_CHARS]


def _is_theta_ja_line(source_line: str) -> bool:
    """Require the cited row to identify junction-to-ambient, and nothing else.

    `RθJA` is Texas Instruments house style. Other vendors write `θJA`, `Theta-JA`, or
    spell out "junction-to-ambient thermal resistance" with no symbol at all, so keying
    on TI's exact spelling silently drops every one of them.

    The negative check carries the safety. `RθJC`, `RθJB`, `ψJT` and `ψJB` sit directly
    beneath θJA in the same table and are a *fraction* of it — junction-to-case on the
    TPS54331 is 53.7 against 116.3 — so mistaking one understates the temperature rise,
    which is the direction that passes a board that cooks.
    """
    if _OTHER_METRICS.search(source_line):
        return False
    return bool(_THETA_JA_NAMES.search(source_line))


def _fact_from_reply(
    reply: Mapping[str, Any], text: str, package: str
) -> ThermalFact | None:
    value = reply.get("theta_ja")
    source_line = reply.get("source_line")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 5 <= value <= 500 or not isinstance(source_line, str) or not source_line.strip():
        return None
    if not _is_theta_ja_line(source_line):
        return None
    if _collapsed(source_line) not in _collapsed(text):
        return None
    return ThermalFact(float(value), source_line.strip(), package)


async def theta_ja_from_text(text: str, *, mpn: str, package: str) -> ThermalFact | None:
    """Extract and evidence-check one θJA figure for an MPN/package pair."""
    cached = _load(mpn)
    if cached is not None:
        return cached
    if not llm.available():
        log.debug("θJA extraction skipped for %s: no LLM configured", mpn)
        return None
    try:
        reply = await llm.complete_json(SYSTEM, _prompt(thermal_window(text), mpn, package))
    except (llm.LLMUnavailable, ValueError, json.JSONDecodeError) as error:
        log.debug("θJA extraction failed for %s: %s", mpn, error)
        return None
    fact = _fact_from_reply(reply, text, package)
    if fact is not None:
        _save(mpn, fact)
    return fact


async def fetch(url: str) -> bytes | None:
    """Fetch one bounded HTTPS PDF, returning no data for ordinary host failures."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        log.debug("datasheet URL rejected: %r", url)
        return None
    try:
        # Redirects are followed, and bounded. Measured 11 Aug: Espressif answers 301 on
        # its own datasheet URL and 200 `application/pdf` one hop later, so refusing to
        # follow drops a vendor whose parts are on real boards here. The final body still
        # has to begin with a PDF magic number, which is what actually guarantees this is
        # a document rather than a login wall.
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT_S, follow_redirects=True, max_redirects=MAX_REDIRECTS
        ) as http:
            async with http.stream("GET", url) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    log.debug("datasheet fetch returned HTTP %s: %s", response.status_code, url)
                    return None
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                # Only markup is rejected outright — that is the shape of the interstitial
                # `datasheet.lcsc.com` serves instead of a PDF, and it can be large. Every
                # other content type is left to the magic-number check, because a host
                # labelling a PDF `application/octet-stream` is still serving a PDF.
                if content_type.startswith("text/"):
                    log.debug("datasheet fetch returned markup, not a PDF: %s", url)
                    return None
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_PDF_BYTES:
                        log.debug("datasheet PDF exceeded size limit: %s", url)
                        return None
                    chunks.append(chunk)
    except Exception as error:  # DNS, TLS, timeout, malformed response: all expected sometimes
        log.debug("datasheet fetch failed for %s: %s", url, error)
        return None

    data = b"".join(chunks)
    if not data.startswith(b"%PDF-"):
        log.debug("datasheet body lacked PDF magic number: %s", url)
        return None
    return data


# ── cache ─────────────────────────────────────────────────────────────────────


def _cache_path(mpn: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in mpn)
    return CACHE_DIR / f"{safe}.json"


def _prompt_version() -> str:
    return hashlib.sha256(SYSTEM.encode()).hexdigest()[:12]


def _load(mpn: str) -> ThermalFact | None:
    path = _cache_path(mpn)
    if not path.exists():
        return None
    try:
        stored = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if stored.get("prompt") != _prompt_version():
        return None
    value = stored.get("theta_ja")
    source_line = stored.get("source_line")
    package_column = stored.get("package_column")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 5 <= value <= 500
        or not isinstance(source_line, str)
        or not isinstance(package_column, str)
    ):
        return None
    return ThermalFact(float(value), source_line, package_column)


def _save(mpn: str, fact: ThermalFact) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(mpn).write_text(
        json.dumps(
            {
                "mpn": mpn,
                "prompt": _prompt_version(),
                "theta_ja": fact.theta_ja,
                "source_line": fact.source_line,
                "package_column": fact.package_column,
            },
            indent=1,
        )
    )
