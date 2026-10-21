"""Bus vocabulary and compatibility facts owned by the constraint engine.

Distributor interface fields are free text. A protocol can reach the engine under a
vendor's transliteration, a conventional abbreviation, or a differently cased spelling;
the rule layer must not turn those presentation differences into electrical conflicts.

The table is deliberately local to ``engine``. It is a constraint-engine fact rather
than a catalogue taxonomy, so it remains deterministic and imports no other module.
"""

from __future__ import annotations


# payload spelling → engine's single protocol name
_ALIASES: dict[str, str] = {
    "SINGLE BUS": "1-WIRE",
    "SINGLE_BUS": "1-WIRE",
    "ONE-WIRE": "1-WIRE",
    "ONEWIRE": "1-WIRE",
    "TWI": "I2C",
    "IIC": "I2C",
    "I²C": "I2C",
}


def canonical_bus(bus: str) -> str:
    """Return a case- and surrounding-whitespace-insensitive bus name.

    Unknown names are still folded to uppercase. This gives equality checks a stable
    representation without falsely claiming that an unfamiliar protocol is known.
    """
    name = bus.strip().upper()
    return _ALIASES.get(name, name)


# peripheral bus → master buses that can safely host it
_MASTER_COMPATIBILITY: dict[str, frozenset[str]] = {
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
    "RS-485": frozenset({"UART"}),
    "RS-422": frozenset({"UART"}),
    "RS-232": frozenset({"UART"}),
    "LIN": frozenset({"UART"}),
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
