"""Recognize explicit brief constraints when model inference is unavailable."""

import re


def battery_primary(text: str) -> bool:
    """An explicit battery-powered brief; USB charging is not a USB-only supply."""
    return bool(re.search(r"battery[- ]powered|power(?:ed)?\s+(?:it\s+)?(?:from|by|with)\s+(?:a\s+)?(?:single\s+)?(?:rechargeable\s+)?(?:\d+(?:\.\d+)?\s*v\s+)?(?:li[- ]?ion\s+)?battery", text, re.I))


def explicit_requirements(text: str) -> dict:
    text = text.lower()
    values = {}
    capacity = re.search(r"\b(\d+(?:\.\d+)?)\s*mAh\b", text, re.I)
    if capacity and 0 < float(capacity[1]) <= 10000000:
        values["supply_capacity_mah"] = float(capacity[1])
    if battery_primary(text) and re.search(r"li[- ]?ion|\b3\.7\s*v\b", text):
        values["input_source"] = "battery-3v7"
        volts = re.search(r"\b(\d+(?:\.\d+)?)\s*v\s+(?:li[- ]?ion\s+)?battery", text)
        if volts:
            values["input_voltage"] = float(volts[1])
    if re.search(r"\bindustrial\b", text):
        values["temp_range"] = (-40, 85)
    temperature = re.search(r"(-?\d+)\s*(?:°?c)?\s*(?:to|~)\s*(-?\d+)\s*°?c\b", text)
    if temperature:
        low, high = map(int, temperature.groups())
        if -273 <= low < high <= 300:
            values["temp_range"] = (low, high)
    stock = re.search(r"\b(\d[\d,]*)\s+(?:units|boards)\b", text)
    if stock:
        quantity = int(stock[1].replace(",", ""))
        if 0 < quantity <= 10_000_000:
            values["min_stock"] = quantity
    lifetime = re.search(r"\b(?:last|lasts|run|runs|operate)\s+(?:for\s+)?(a|an|one|\d+)\s+(hour|day|week|month|year)s?\b", text)
    if lifetime:
        amount = 1 if lifetime[1] in {"a", "an", "one"} else int(lifetime[1])
        hours = amount * {"hour": 1, "day": 24, "week": 168, "month": 730, "year": 8760}[lifetime[2]]
        if 0 < hours <= 87600:
            values["lifetime_hours"] = hours
    return values
