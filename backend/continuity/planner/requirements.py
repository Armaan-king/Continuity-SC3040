"""Recognize explicit brief constraints when model inference is unavailable."""

import re


def explicit_requirements(text: str) -> dict:
    text = text.lower()
    values = {}
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
