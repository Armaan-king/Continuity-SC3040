"""Short, stable project titles without a model call or changes to the brief."""

from __future__ import annotations

import re

UNTITLED = "Untitled board"

_INTRO = re.compile(
    r"^(?:please\s+|(?:can|could|would)\s+you\s+|help\s+me\s+|"
    r"(?:i|we)\s+(?:want|need|would\s+like|am\s+trying|are\s+trying)\s+(?:to\s+)?|"
    r"(?:i['’]d|we['’]d)\s+like\s+(?:to\s+)?|"
    r"(?:make|build|create|design|develop)\s+(?:me\s+)?|"
    r"(?:a|an|the|small|simple|basic|compact)\s+)", re.IGNORECASE,
)
_ACRONYMS = {word.lower(): word for word in (
    "USB", "USB-C", "BLE", "GPS", "LoRa", "WiFi", "I2C", "I²C", "SPI", "PCB",
    "LED", "OLED", "PoE", "RF", "MCU", "BOM", "ESP32", "IoT",
)}


def project_name(brief: str) -> str:
    text = " ".join(brief.split()).strip(' \"\'')
    while match := _INTRO.match(text):
        text = text[match.end():]
    # Constraints belong to the unchanged brief, not to the project label.
    text = re.split(r"[,;!?]|\.(?:\s|$)|\s+(?:with|using|that|which|powered\s+by|must|should)\b",
                    text, maxsplit=1, flags=re.IGNORECASE)[0]
    words = text.strip(" .:-").split()[:8]
    while words and words[-1].lower() in {"a", "an", "the", "and", "or", "for", "to", "of"}:
        words.pop()
    words = [_ACRONYMS.get(word.lower(), word if any(c.isupper() for c in word[1:])
                          else word[:1].upper() + word[1:]) for word in words]
    while words and len(" ".join(words)) > 64:
        words.pop()
    return " ".join(words) or "Circuit Design"


async def upgrade_legacy_names(conn) -> None:
    """One-time repair of titles matching the old frontend's exact naming algorithm."""
    cursor = await conn.execute("""
        SELECT p.id, p.name, t.prompt FROM projects p
        LEFT JOIN LATERAL (
            SELECT prompt FROM threads WHERE project_id = p.id ORDER BY created_at LIMIT 1
        ) t ON true
        WHERE NOT p.is_walkthrough
    """
    )
    for project_id, name, prompt in await cursor.fetchall():
        legacy = " ".join((prompt or "").split()[:6])[:64]
        conversational = re.match(r"^(?:i\b|we\b|please\b|can\b|could\b|help\b|make\b|build\b|create\b|design\b)", legacy, re.I)
        if name != UNTITLED and (not prompt or name != legacy or not conversational):
            continue
        title = project_name(prompt) if prompt else UNTITLED
        await conn.execute(
            "UPDATE projects SET name = %s, name_source = 'generated' WHERE id = %s AND name = %s",
            (title, project_id, name),
        )
