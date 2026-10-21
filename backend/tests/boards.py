"""Board builders for the engine tests."""

from __future__ import annotations

from continuity.engine.models import Board, PartSpec, Rail, Requirements, Slot

USB_C_CURRENT_LIMIT = 3.0

TIERS = {
    "regulator": ("Regulator", "power"),
    "mcu": ("Microcontroller", "core"),
    "sensor": ("Temp / Humidity Sensor", "peripherals"),
    "display": ("OLED Display", "peripherals"),
    "flash": ("Serial Flash", "peripherals"),
}


def slot(slot_id: str, part: PartSpec | None = None, *, pinned: bool = False, **kw) -> Slot:
    label, tier = TIERS.get(slot_id, (slot_id.title(), "peripherals"))
    return Slot(
        id=slot_id,
        label=kw.pop("label", label),
        tier=kw.pop("tier", tier),
        pinned=pinned,
        part=part,
        status="pass" if part is not None else "pending",
        **kw,
    )


def usb_board(
    *,
    regulator: PartSpec,
    loads: dict[str, PartSpec],
    pinned: tuple[str, ...] = ("mcu", "sensor", "display"),
    requirements: Requirements | None = None,
    rail_voltage: float = 3.3,
    input_voltage: float = 5.0,
) -> Board:
    """USB-C 5 V in, one regulated rail out, everything else hanging off it.

    The shape of the demo board, and of most small boards: the only slot nobody asked
    for is the regulator, which is precisely why the fence keeps landing on it.
    """
    slots = {"regulator": slot("regulator", regulator, pinned="regulator" in pinned)}
    for slot_id, part in loads.items():
        slots[slot_id] = slot(slot_id, part, pinned=slot_id in pinned)

    rails = {
        "5V0": Rail(
            id="5V0",
            voltage=input_voltage,
            source=None,
            members=("regulator",),
            i_limit=USB_C_CURRENT_LIMIT,
        ),
        "3V3": Rail(
            id="3V3",
            voltage=rail_voltage,
            source="regulator",
            members=tuple(loads),
        ),
    }
    return Board(
        requirements=requirements or Requirements(),
        slots=slots,
        rails=rails,
    )
