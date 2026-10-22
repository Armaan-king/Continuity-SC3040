"""Bus vocabulary and compatibility facts owned by the constraint engine.

Distributor interface fields are free text. A protocol can reach the engine under a
vendor's transliteration, a conventional abbreviation, or a differently cased spelling;
the rule layer must not turn those presentation differences into electrical conflicts.

The table is deliberately local to ``engine``. It is a constraint-engine fact rather
than a catalogue taxonomy, so it remains deterministic and imports no other module.
"""

from __future__ import annotations

import re
import unicodedata


# payload spelling → engine's single protocol name
_ALIASES: dict[str, str] = {
    "SINGLE BUS": "1-WIRE",
    "SINGLE_BUS": "1-WIRE",
    "ONE-WIRE": "1-WIRE",
    "ONEWIRE": "1-WIRE",
    "TWI": "I2C",
    "IIC": "I2C",
    "I²C": "I2C",
    "I²S": "I2S",
    "1WIRE": "1-WIRE",
    "1 WIRE": "1-WIRE",
    "ONE WIRE": "1-WIRE",
    "RS232": "RS-232",
    "RS422": "RS-422",
    "RS485": "RS-485",
    "CANFD": "CAN-FD",
    "CAN FD": "CAN-FD",
}

_INTERFACE_TOKEN = re.compile(
    r"(?<!\w)(?:I2C|IIC|I²C|TWI|I2S|I²S|SPI|USART|UART|CAN[- ]?FD|CAN|USB|SMBUS|LIN|"
    r"1[- ]?WIRE|ONE[- ]?WIRE|SINGLE[ _]BUS|RS-?(?:232|422|485))(?![\w-])", re.I)


def _spelling(value: str) -> str:
    # Presentation variants only; never edit-distance/fuzzy protocol matching.
    value = unicodedata.normalize('NFKC', value).translate(str.maketrans('−–—‑‐‒', '------'))
    return re.sub(r'\bI\s*[- ]?\s*2\s*[- ]?\s*([CS])\b', r'I2\1', value, flags=re.I)


def listed_interfaces(value: str) -> tuple[str, ...]:
    """Read explicit interface-list tokens using the engine's shared vocabulary.

    Prose negating or qualifying a capability is not positive evidence. Unknown
    names and generic phrases such as 'high-speed peripheral interface' stay unknown.
    """
    found = []
    for segment in re.split(r"[,;、|/\n]", _spelling(value)):
        if re.search(r"\b(?:no|not|without|unsupported|optional)\b", segment, re.I):
            continue
        found.extend(canonical_bus(match[0]) for match in _INTERFACE_TOKEN.finditer(segment))
    return tuple(dict.fromkeys(found))


def canonical_bus(bus: str) -> str:
    """Return a case- and surrounding-whitespace-insensitive bus name.

    Unknown names are still folded to uppercase. This gives equality checks a stable
    representation without falsely claiming that an unfamiliar protocol is known.
    """
    name = _spelling(bus).strip().upper()
    return _ALIASES.get(name, name)


# peripheral bus → master buses that can safely host it
_MASTER_COMPATIBILITY: dict[str, frozenset[str]] = {
    # USART provides asynchronous UART operation. The reverse does not establish
    # a synchronous USART connection (Arm CMSIS USART mode-control vocabulary).
    "UART": frozenset({"USART"}),
    "SMBUS": frozenset({"I2C"}),
    # A transceiver has two sides, and the distributor names the wrong one for this
    # check. `SP3485EN-L/TR` advertises `RS-485` and `RS-422` — its *line* side, the pair
    # of wires leaving the board. What it needs from the controller is a plain UART.
    #
    # No microcontroller has ever exposed "RS-485" as a peripheral, so equality alone made
    # every RS-485 board fail a check no replacement could clear: swap the transceiver and
    # the new one advertises RS-485 too. Measured 13 Aug, where the reviewer diagnosed it
    # correctly and escalated because there was nothing on either slot left to try.
    #
    # CAN escaped only by luck of naming — a CAN controller really is called CAN, so the
    # line-side and host-side names happen to match. These four have no such coincidence.
    "RS-485": frozenset({"UART", "USART"}),
    "RS-422": frozenset({"UART", "USART"}),
    "RS-232": frozenset({"UART", "USART"}),
    "LIN": frozenset({"UART", "USART"}),
}
"""Directed, not symmetric: SMBus devices work on I²C masters, but I²C devices are
not reliably safe on SMBus-only masters. Equality is handled separately below.

The direction matters for the transceivers too. An RS-485 part is satisfied by a UART
controller; a UART peripheral is *not* satisfied by an RS-485 line, which is a pair of
differential wires and not a controller at all."""


def master_satisfies_bus(peripheral_bus: str, master_bus: str) -> bool:
    """Whether one peripheral interface is usable with one offered master interface."""
    peripheral = canonical_bus(peripheral_bus)
    master = canonical_bus(master_bus)
    return peripheral == master or master in _MASTER_COMPATIBILITY.get(peripheral, ())
