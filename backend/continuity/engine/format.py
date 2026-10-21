"""Number formatting for verdict sentences.

Verdict `detail` strings are rendered on screen verbatim, so they are written for a
person reading them once, in a demo, at a distance: units always, trailing zeros never,
and the same quantity formatted the same way everywhere.
"""

from __future__ import annotations


def num(value: float, places: int = 1) -> str:
    """Fixed-point, with trailing zeros trimmed: 600.0 → '600', 700.55 → '700.6'."""
    text = f"{value:.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def milliamps(amps: float) -> str:
    return f"{num(amps * 1000)} mA"


def volts(value: float) -> str:
    return f"{num(value, 2)} V"


def watts(value: float) -> str:
    return f"{num(value, 2)} W"


def celsius(value: float) -> str:
    return f"{num(value, 0)} °C"


def percent(fraction: float) -> str:
    """'<1%' rather than '0%' for a small non-zero share — 0% reads as 'nothing'."""
    if 0 < fraction < 0.005:
        return "<1%"
    return f"{round(fraction * 100)}%"


def ohms_per_watt(value: float) -> str:
    return f"{num(value, 0)} °C/W"


def count(value: int) -> str:
    return f"{value:,}"


def listing(items: "list[str] | tuple[str, ...]") -> str:
    """'I2C', 'I2C and SPI', 'I2C, SPI and UART'."""
    items = list(items)
    if not items:
        return "nothing"
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def plural(n: int, singular: str, suffix: str = "s") -> str:
    return f"{n} {singular}{'' if n == 1 else suffix}"


def duration(hours: float) -> str:
    """A span of hours in the unit a person would use for it.

    A battery lifetime spans six orders of magnitude between a WiFi module on a coin cell
    and an e-paper tag, and "8760 hours" against "3.2 hours" makes a reader do the
    arithmetic that the verdict exists to save them.
    """
    if hours < 1:
        return plural(max(1, round(hours * 60)), "minute")
    if hours < 48:
        return plural(round(hours), "hour")

    days = hours / 24
    if days < 60:
        return plural(round(days), "day")
    if days < 365:
        return plural(round(days / 30.44), "month")

    years = num(days / 365.25)
    return f"{years} year{'' if years == '1' else 's'}"
