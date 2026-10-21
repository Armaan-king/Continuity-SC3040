"""Package lookup tables — thermal resistance and nominal body size.

## Why this exists, and what it is not

θJA is almost never a distributor parameter. It lives in the datasheet, often in a
table with several values for different board constructions. R5 needs a number, so we
keep one here.

**These are approximations and every verdict that uses one says so.** θJA is a
board-level property, not a package property: the same SOT-23-5 measures ~250 °C/W on
a JEDEC low-K single-layer board and nearer 180 °C/W on a 2s2p board with copper pour
(JESD51-2 / JESD51-7). The values below are the *low-K, single-layer* case, which is
the conservative direction — it over-estimates temperature rise rather than under-.

This matters on stage. When R5 fails a part, the evidence rows cite the package
verbatim from the distributor payload, and cite θJA against `THETA_JA_SOURCE` — never
against the datasheet URL, because the datasheet did not say it. An engine that claims
a source it does not have is worth less than one that admits the gap.

Body sizes are nominal, body-only, excluding leads. They back the warning-only
`footprint` rule; nothing hard depends on them.
"""

from __future__ import annotations

import re

THETA_JA_SOURCE = "Continuity package table (JESD51-2 low-K board, approximate)"

_ALNUM = re.compile(r"[^A-Z0-9]")


def _key(package: str) -> str:
    """Fold package spellings together: 'SOT-23-5', 'SOT23-5', 'sot 23 5' → 'SOT235'."""
    return _ALNUM.sub("", package.upper())


def _parenthetical_aliases(package: str) -> tuple[str, ...]:
    """The outside spelling comes before a parenthetical package alias."""
    match = re.search(r"\(([^()]*)\)", package)
    if not match:
        return (package,)
    return (package[: match.start()] + package[match.end() :], match.group(1))


def _spelling_aliases(key: str) -> tuple[str, ...]:
    """Known distributor spellings for packages already present in the tables."""
    aliases: list[str] = []
    if key.startswith("SOT23THIN"):
        aliases.append("TSOT23" + key.removeprefix("SOT23THIN"))
    if key.startswith("TSOT"):
        aliases.append("SOT" + key.removeprefix("TSOT"))
    if key.startswith("VQFN"):
        aliases.append("QFN" + key.removeprefix("VQFN"))
    if key.startswith("SOP"):
        aliases.append("SOIC" + key.removeprefix("SOP"))
    if re.match(r"SO\d", key):
        aliases.append("SOIC" + key.removeprefix("SO"))

    match = re.fullmatch(r"[EH]SOP(\d+)", key)
    if match:
        aliases.append(f"SOIC{match.group(1)}EP")
    match = re.fullmatch(r"[EH]TSSOP(\d+)", key)
    if match:
        aliases.append(f"TSSOP{match.group(1)}EP")
    return tuple(aliases)


def _is_exposed_pad(key: str) -> bool:
    """Whether a folded key explicitly describes a package with a thermal pad."""
    return bool(re.search(r"EP(?:\d|$)", key) or re.match(r"^[EH](?:SOP|TSSOP)\d", key))


def _candidate_keys(package: str) -> tuple[str, ...]:
    """Package keys, ordered from the supplied spelling to progressively broader aliases."""
    candidates: list[str] = []
    for spelling in _parenthetical_aliases(package):
        key = _key(spelling)
        variants = [key]
        if re.search(r"\dL$", key):
            variants.append(key[:-1])
        if variants[-1][-1:].isdigit():
            variants.append(variants[-1][:-1])

        for variant in variants:
            aliases = [variant]
            for alias in aliases:
                aliases.extend(
                    candidate
                    for candidate in _spelling_aliases(alias)
                    if candidate not in aliases
                )
            candidates.extend(candidate for candidate in aliases if candidate not in candidates)
    return tuple(candidates)


def family(package: str | None) -> str | None:
    """The package's alphabetic family — SOT-223 → SOT, VQFN-16 → QFN."""
    if not package:
        return None
    key = _key(package)
    if not key:
        return None
    aliases = _spelling_aliases(key)
    canonical = aliases[0] if aliases else key
    match = re.match(r"[A-Z]+", canonical)
    if match is None:
        return None
    candidate = match.group()
    known = {
        family_match.group()
        for table_key in (*_THETA_JA, *_BODY_MM)
        if (family_match := re.match(r"[A-Z]+", table_key)) is not None
    }
    return candidate if candidate in known else None


# package → θJA in °C/W
_THETA_JA: dict[str, float] = {
    # small-outline transistor — the linear-regulator workhorses, and thermally awful
    "SOT23": 250.0,
    "SOT233": 250.0,
    "SOT235": 250.0,
    "SOT236": 240.0,
    "TSOT235": 200.0,
    "SOT353": 300.0,
    "SC705": 300.0,
    "SOT563": 250.0,
    "SOT89": 140.0,
    "SOT893": 140.0,
    "SOT223": 62.0,
    "SOT2233": 62.0,
    # tabbed power packages
    "TO252": 92.0,
    "DPAK": 92.0,
    "TO263": 50.0,
    "D2PAK": 50.0,
    # small-outline / shrink-outline
    "SOIC8": 120.0,
    "SO8": 120.0,
    # SOIC-8-EP / SO-8-EP / SOP-8-EP / ESOP-8 / HSOP-8, low-K approximation
    "SOIC8EP": 65.0,
    "MSOP8": 210.0,
    "TSSOP8": 150.0,
    "TSSOP14": 110.0,
    "TSSOP16": 100.0,
    # leadless — thermal pad down to the board, hence the step change
    "DFN6": 60.0,
    "SON6": 60.0,
    "WSON8": 50.0,
    "VSON8": 55.0,
    "VSONHR8": 55.0,
    "QFN16": 45.0,
    "QFN20": 40.0,
    "QFN24": 38.0,
    "QFN32": 34.0,
}

# package → nominal body (length_mm, width_mm)
_BODY_MM: dict[str, tuple[float, float]] = {
    "SOT23": (2.9, 1.6),
    "SOT233": (2.9, 1.6),
    "SOT235": (2.9, 1.6),
    "SOT236": (2.9, 1.6),
    "TSOT235": (2.9, 1.6),
    "SOT353": (2.0, 1.25),
    "SC705": (2.0, 1.25),
    "SOT563": (1.6, 1.2),
    "SOT89": (4.5, 2.5),
    "SOT893": (4.5, 2.5),
    "SOT223": (6.5, 3.5),
    "SOT2233": (6.5, 3.5),
    "TO252": (6.5, 6.1),
    "DPAK": (6.5, 6.1),
    "TO263": (10.0, 9.9),
    "D2PAK": (10.0, 9.9),
    "SOIC8": (4.9, 3.9),
    "SO8": (4.9, 3.9),
    "MSOP8": (3.0, 3.0),
    "TSSOP8": (3.0, 4.4),
    "TSSOP14": (5.0, 4.4),
    "TSSOP16": (5.0, 4.4),
    "DFN6": (3.0, 3.0),
    "SON6": (3.0, 3.0),
    "WSON8": (4.0, 4.0),
    "VSON8": (2.0, 1.5),
    "VSONHR8": (2.0, 1.5),
    "QFN16": (4.0, 4.0),
    "QFN20": (4.0, 4.0),
    "QFN24": (4.0, 4.0),
    "QFN32": (5.0, 5.0),
}


def theta_ja(package: str | None) -> float | None:
    """θJA in °C/W for a package name, or None if we have no entry for it.

    None is a real answer — R5 reports that it could not evaluate rather than
    substituting a default and producing a confident number from nothing.
    """
    if not package:
        return None
    exposed_pad = _is_exposed_pad(_key(package))
    for candidate in _candidate_keys(package):
        if not exposed_pad or _is_exposed_pad(candidate):
            theta = _THETA_JA.get(candidate)
            if theta is not None:
                return theta
    return None


def body_mm(package: str | None) -> tuple[float, float] | None:
    """Nominal body dimensions in mm, or None if unknown."""
    if not package:
        return None
    exposed_pad = _is_exposed_pad(_key(package))
    for candidate in _candidate_keys(package):
        if not exposed_pad or _is_exposed_pad(candidate):
            body = _BODY_MM.get(candidate)
            if body is not None:
                return body
    return None


def longest_side_mm(package: str | None) -> float | None:
    dims = body_mm(package)
    return max(dims) if dims else None


def known_packages() -> tuple[str, ...]:
    return tuple(sorted(_THETA_JA))
