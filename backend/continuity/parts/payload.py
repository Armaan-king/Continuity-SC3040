"""Reading a distributor's raw strings well enough to choose what to look at.

## Why this exists alongside the normaliser

`normalize.py` turns a payload into a `PartSpec` with provenance, using a model, one call
per part. That is what *verdicts* are computed from and it is deliberately expensive: it
runs on the one candidate the engine is about to check.

This module does something different and much cheaper. It reads a value straight out of
the payload so the system can decide **which candidates are worth normalising at all**.

The distinction matters and it is the whole reason this is safe:

- Nothing here ever reaches a rule. A value parsed here selects and orders candidates;
  the engine still checks the normalised `PartSpec`, which carries provenance and was
  validated field by field.
- So a wrong parse costs a slightly worse candidate list, never a wrong verdict. That is
  a good place for a cheap heuristic, and a terrible place for one is anywhere downstream.

## Why the search API cannot do this instead

JLCPCB stores a whole range in a single string — `"Voltage - Supply": "4.5V~40V"` — and a
spec filter over it matches the range's **minimum**. Measured live:

    Voltage - Supply >= 48V   →  0 hits

because no buck has a *minimum* input above 48 V. So "must accept 48 V" is inexpressible
as a filter, and the same is true of every other "must be rated up to X" constraint. The
payload has the ceiling in it; it only needs splitting.

(Worth knowing: an unrecognised filter name is silently ignored by the server rather than
rejected, so inventing a parameter like `Voltage - Input (Max)` returns *unfiltered*
results and looks like it worked.)
"""

from __future__ import annotations

import re
from typing import Mapping

SUPPLY_PARAMETER = "Voltage - Supply"
OUTPUT_PARAMETER = "Output Voltage"
OPERATING_TEMPERATURE_PARAMETER = "Operating Temperature"

_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6}

_FIGURE = re.compile(r"([-+]?\d*\.?\d+)\s*([pnuµμmkM]?)V", re.IGNORECASE)
"""A voltage with an optional SI prefix. Case-sensitive on the prefix would be nicer —
`m` and `M` differ by a billion — but distributor strings are not written that carefully,
and supply voltages are never in megavolts, so the ambiguity does not bite here."""


def _figure(text: str) -> float | None:
    match = _FIGURE.search(text)
    if match is None:
        return None
    value = float(match.group(1))
    prefix = match.group(2)
    # Only scale down. "40MV" is a typo for 40 V, not 40 megavolts.
    return value * _SI.get(prefix, 1.0) if prefix and _SI.get(prefix, 1.0) < 1 else value


_TEMPERATURE_FIGURE = re.compile(r"([-+]?\d*\.?\d+)\s*℃")
"""A Celsius figure as JLCPCB writes it, using its non-ASCII degree sign."""


def _temperature_figure(text: str) -> float | None:
    match = _TEMPERATURE_FIGURE.search(text)
    return float(match.group(1)) if match is not None else None


_BARE_FIGURE = re.compile(r"^\s*([-+]?\d*\.?\d+)\s*$")
"""A number and nothing else. Read only as one half of an already-split range, where the
surrounding string has established that the figure is a temperature — anything carrying a
qualifier such as `@(TJ)` fails to match rather than being guessed at."""


def volt_range(text: str | None) -> tuple[float | None, float | None]:
    """`"4.5V~40V"` → `(4.5, 40.0)`. A single figure is a ceiling, per the same reading
    the normaliser is given: `"15V"` on a supply states a maximum, not a minimum.

    `(None, None)` when nothing usable is there. Unknown, never zero.
    """
    if not text:
        return (None, None)

    parts = [p for p in re.split(r"[~–—]", str(text)) if p.strip()]
    if len(parts) >= 2:
        return (_figure(parts[0]), _figure(parts[1]))
    return (None, _figure(str(text)))


def accepts_input(specs: Mapping[str, str], voltage: float) -> bool | None:
    """Can this part be fed `voltage`? `None` when the payload does not say.

    `None` keeps the candidate. An unknown rating is something the engine will report as
    unchecked; discarding the part here would turn "we cannot tell" into "no", which is
    the one direction this system is not allowed to guess in.
    """
    low, high = volt_range(specs.get(SUPPLY_PARAMETER))
    if high is not None and voltage > high:
        return False
    if low is not None and voltage < low:
        return False
    if low is None and high is None:
        return None
    return True


def rated_to(specs: Mapping[str, str], celsius: float) -> bool | None:
    """Does the payload state an operating-temperature maximum reaching `celsius`?

    `None` keeps the candidate when the payload does not state a usable maximum; the
    engine will report that as unchecked rather than treating unknown as a rejection.
    A trailing qualifier such as `@(TJ)` is ignored after reading the range. `(TJ)`
    marks junction temperature, not ambient temperature, but the normaliser already
    folds both into `temp_max` and R7 compares that to ambient requirements. This local
    filter records that limitation without inventing a distinction the engine lacks.
    """
    text = specs.get(OPERATING_TEMPERATURE_PARAMETER)
    if not text:
        return None

    parts = [part for part in re.split(r"[~–—]", str(text)) if part.strip()]
    maximum = _temperature_figure(parts[1]) if len(parts) >= 2 else _temperature_figure(str(text))
    if maximum is None:
        return None
    return celsius <= maximum


def rated_from(specs: Mapping[str, str], celsius: float) -> bool | None:
    """Does the payload state an operating-temperature minimum reaching down to `celsius`?

    The cold-end twin of `rated_to`, and the reason a repair could not escape an outdoor
    brief. R7 detects a −40 °C violation perfectly well, but nothing could act on it: the
    re-search had no way to exclude the part that had just failed, so it came back, failed
    again, and the run escalated to the user with a question the user could not answer
    either.

    `None` when the payload does not state a usable minimum, keeping the candidate — the
    engine reports that as unchecked. A single figure states no minimum, so it reads
    `None` rather than being mistaken for one.
    """
    text = specs.get(OPERATING_TEMPERATURE_PARAMETER)
    if not text:
        return None

    parts = [part for part in re.split(r"[~–—]", str(text)) if part.strip()]
    if len(parts) < 2:
        return None
    # `-25~85℃` carries the unit once, on the end. The hot end never noticed because it
    # reads the second half; the cold end reads the first and found no degree sign at all.
    minimum = _temperature_figure(parts[0])
    if minimum is None:
        bare = _BARE_FIGURE.match(parts[0])
        minimum = float(bare.group(1)) if bare else None
    if minimum is None:
        return None
    return celsius >= minimum


ADJUSTABLE_OUTPUT = "adjustable"


def is_adjustable(specs: Mapping[str, str]) -> bool:
    """Does the distributor say this part's output is set by the designer?

    JLCPCB publishes `Output Type: Fixed | Adjustable`, and it decides what a single
    output figure *means*. TPS61040DBVR is an adjustable boost covering 1.8–28 V and
    lists "Output Voltage: 28V" — the top of the range, not a setpoint.
    """
    return ADJUSTABLE_OUTPUT in str(specs.get("Output Type", "")).strip().lower()


def can_output(specs: Mapping[str, str], voltage: float) -> bool | None:
    """Can this part be set to make `voltage`? `None` when the payload does not say.

    The payload-level twin of `PartSpec.produces`, and it reads a single figure the same
    way: fixed means both ends, adjustable means a ceiling with an unstated floor. Kept
    in step deliberately — a candidate this drops would have failed R1 anyway, and a
    candidate this keeps is one the engine can still judge for itself.
    """
    low, high = volt_range(specs.get(OUTPUT_PARAMETER))
    if low is None and high is not None and not is_adjustable(specs):
        low = high  # a fixed part quoting one figure is fixed at it

    if high is not None and voltage > high:
        return False
    if low is not None and voltage < low:
        return False
    if low is None or high is None:
        return None
    return True


def states_output_range(specs: Mapping[str, str]) -> bool:
    """Does the payload say enough for R1 to *decide* whether this part makes a rail?

    Used to rank, never to reject. Three valid boost converters came back for one slot
    and the engine took the first, whose payload states no output minimum — so the
    board's central claim ended up `unchecked` while two alternatives were provable.

    Decidable means the same thing here as in `PartSpec.produces`: both ends known. A
    fixed part quoting one figure has both (they are equal); an adjustable one quoting
    one figure has only a ceiling, and no bound on what it can be turned down to.
    """
    low, high = volt_range(specs.get(OUTPUT_PARAMETER))
    if low is not None and high is not None:
        return True
    return high is not None and not is_adjustable(specs)
