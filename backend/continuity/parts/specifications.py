"""Conservative parsing of explicit distributor measurements without a model.

Only recognized parameter names and complete unit-bearing values are accepted.
Nominal voltages are not promoted to operating ranges, and missing facts stay missing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

NUMBER = r"([+-]?(?:\d+(?:\.\d+)?|\.\d+))"
SCALE = {"": 1.0, "m": 1e-3, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "n": 1e-9}


def measurements(value: str, unit: str) -> list[float]:
    expression = NUMBER + r"\s*([muµμn]?)\s*" + unit
    single = re.fullmatch(expression, value.strip(), re.I)
    if single:
        return [float(single[1]) * SCALE[single[2].lower()]]
    span = re.fullmatch(expression + r"\s*(?:~|to|–|-)\s*" + expression, value.strip(), re.I)
    if span:
        low = float(span[1]) * SCALE[span[2].lower()]
        high = float(span[3]) * SCALE[span[4].lower()]
        return [low, high] if low <= high else []
    return []


def parse(specs: Mapping[str, str]) -> tuple[dict, dict]:
    fields: dict = {}
    provenance: dict = {}

    def put(key: str, value, source: str) -> None:
        fields[key] = value
        provenance[key] = source

    fixed = any(name.lower() == "output type" and value.lower().strip() == "fixed"
                for name, value in specs.items() if isinstance(value, str))
    for source, raw in specs.items():
        if not isinstance(raw, str):
            continue
        name = re.sub(r"\s+", " ", source.lower().replace(" - ", " ")).strip()
        if name in {"voltage supply", "supply voltage", "operating voltage", "voltage input", "input voltage"}:
            values = measurements(raw, "V")
            if len(values) == 2 and values[0] >= 0:
                put("vmin", values[0], source)
                put("vmax", values[1], source)
        elif name in {"voltage output", "output voltage"}:
            values = measurements(raw, "V")
            if len(values) == 2 and values[0] >= 0:
                put("vout_min", values[0], source)
                put("vout_max", values[1], source)
            elif len(values) == 1 and fixed and values[0] >= 0:
                put("vout_min", values[0], source)
                put("vout_max", values[0], source)
        elif name in {"current supply", "supply current", "operating current", "current output", "output current"}:
            values = measurements(raw, "A")
            if len(values) == 1 and values[0] >= 0:
                put("i_max" if "output" in name else "i_typ", values[0], source)
        elif name in {"operating temperature", "temperature range"}:
            values = measurements(raw.replace("℃", "°C"), "°C")
            if len(values) == 2 and -273.15 <= values[0] < values[1]:
                put("temp_min", values[0], source)
                put("temp_max", values[1], source)
        elif name in {"number of i/o", "number of gpio"} and raw.strip().isdigit():
            put("pins_available", int(raw), source)
        elif name in {"interface", "interface type"}:
            buses = tuple(dict.fromkeys(re.findall(r"\b(?:I2C|SPI|UART|CAN|USB|I2S)\b", raw.upper())))
            if buses:
                put("interfaces", buses, source)
    return fields, provenance
