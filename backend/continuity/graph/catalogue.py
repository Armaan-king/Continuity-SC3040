"""A fixed catalogue of plausible parts. **No longer used in production.**

`graph/sourcing.py` fetches real parts now. This survives as the offline stand-in for
the test suite — see `tests/conftest.py` — because driving the graph through a real
distributor and a real model takes seconds per run, and the graph tests are about the
loop and the wire, not about JLCPCB's inventory.

Every part here is a plausible real part with plausible real numbers, but none of it
was fetched. Nothing should import it outside tests.
"""

from __future__ import annotations

from ..engine.models import PartSpec

_D = "https://datasheet.lcsc.com/lcsc/placeholder.pdf"


def _p(**kw) -> PartSpec:
    kw.setdefault("distributor", "JLCPCB")
    kw.setdefault("lifecycle", "active")
    kw.setdefault("datasheet", _D)
    return PartSpec(**kw)


CATALOGUE: dict[str, list[PartSpec]] = {
    # Each list is what search would return, best-guess first. A repair advances the
    # index, which is how the stub imitates a re-search under a tightened constraint.
    "regulator": [
        _p(
            mpn="AP2114H-3.3TRG1", manufacturer="Diodes Incorporated",
            description="600mA low-dropout regulator, fixed 3.3V", category="LDO Regulator",
            vmin=2.5, vmax=6.0, vout_min=3.3, vout_max=3.3, i_max=0.600, role="passive",
            package="SOT-223", topology="ldo", temp_min=-40, temp_max=125,
            unit_price=0.16, stock=31500, lead_time_days=0,
            raw={"Voltage - Input": "2.5V ~ 6.0V", "Voltage - Output": "3.3V",
                 "Current - Output": "600mA", "Package / Case": "SOT-223",
                 "Operating Temperature": "-40°C ~ 125°C", "Stock": "31500"},
            provenance={"vmin": "Voltage - Input", "vmax": "Voltage - Input",
                        "vout_min": "Voltage - Output", "vout_max": "Voltage - Output", "i_max": "Current - Output",
                        "package": "Package / Case", "temp_max": "Operating Temperature",
                        "stock": "Stock"},
        ),
        _p(
            mpn="AP7361C-33E", manufacturer="Diodes Incorporated",
            description="1A low-dropout regulator, fixed 3.3V", category="LDO Regulator",
            vmin=2.5, vmax=6.0, vout_min=3.3, vout_max=3.3, i_max=1.000, role="passive",
            package="SOT-23-5", topology="ldo", temp_min=-40, temp_max=125,
            unit_price=0.21, stock=9800, lead_time_days=0,
            raw={"Voltage - Input": "2.5V ~ 6.0V", "Voltage - Output": "3.3V",
                 "Current - Output": "1A", "Package / Case": "SOT-23-5",
                 "Operating Temperature": "-40°C ~ 125°C", "Stock": "9800"},
            provenance={"vmin": "Voltage - Input", "vmax": "Voltage - Input",
                        "vout_min": "Voltage - Output", "vout_max": "Voltage - Output", "i_max": "Current - Output",
                        "package": "Package / Case", "temp_max": "Operating Temperature",
                        "stock": "Stock"},
        ),
        _p(
            mpn="TPS62825DMQR", manufacturer="Texas Instruments",
            description="1A synchronous step-down converter, fixed 3.3V",
            category="Buck Regulator",
            vmin=2.7, vmax=6.0, vout_min=3.3, vout_max=3.3, i_max=1.000, role="passive",
            package="VSON-HR-8", topology="buck", efficiency=0.92,
            temp_min=-40, temp_max=125, unit_price=0.68, stock=8200, lead_time_days=0,
            raw={"Voltage - Input": "2.7V ~ 6.0V", "Voltage - Output": "3.3V",
                 "Current - Output": "1A", "Efficiency": "92%",
                 "Package / Case": "VSON-HR-8", "Operating Temperature": "-40°C ~ 125°C",
                 "Stock": "8200"},
            provenance={"vmin": "Voltage - Input", "vmax": "Voltage - Input",
                        "vout_min": "Voltage - Output", "vout_max": "Voltage - Output", "i_max": "Current - Output",
                        "efficiency": "Efficiency", "package": "Package / Case",
                        "temp_max": "Operating Temperature", "stock": "Stock"},
        ),
    ],
    "mcu": [
        _p(
            mpn="ESP32-S3-WROOM-1-N8R2", manufacturer="Espressif Systems",
            description="WiFi + BLE 5.0 module, 8MB flash", category="RF Module",
            vmin=3.0, vmax=3.6, i_typ=0.100, i_peak=0.500,
            interfaces=("I2C", "SPI", "UART"), role="master", pins_available=36,
            package="Module", temp_min=-40, temp_max=85,
            unit_price=2.89, stock=18432, lead_time_days=0,
            raw={"Voltage - Supply": "3.0V ~ 3.6V", "Current - Supply (Max)": "500mA",
                 "Interface": "I2C, SPI, UART", "Number of I/O": "36", "Stock": "18432"},
            provenance={"vmin": "Voltage - Supply", "vmax": "Voltage - Supply",
                        "i_peak": "Current - Supply (Max)", "interfaces": "Interface",
                        "pins_available": "Number of I/O", "stock": "Stock"},
        ),
    ],
    "sensor": [
        _p(
            mpn="SHT40-AD1B-R2", manufacturer="Sensirion",
            description="Digital temperature and humidity sensor", category="Humidity Sensor",
            vmin=1.08, vmax=3.6, i_typ=0.0000026, i_peak=0.000320,
            interfaces=("I2C",), role="peripheral", pins_required=2,
            package="DFN-4", temp_min=-40, temp_max=125,
            unit_price=2.31, stock=0, lead_time_days=56,
            raw={"Voltage - Supply": "1.08V ~ 3.6V", "Current - Supply (Max)": "320uA",
                 "Interface": "I2C", "Stock": "0", "Lead Time": "56 days"},
            provenance={"vmin": "Voltage - Supply", "vmax": "Voltage - Supply",
                        "i_peak": "Current - Supply (Max)", "interfaces": "Interface",
                        "stock": "Stock", "lead_time_days": "Lead Time"},
        ),
        _p(
            mpn="SHT31-DIS-B", manufacturer="Sensirion",
            description="Digital temperature and humidity sensor", category="Humidity Sensor",
            vmin=2.15, vmax=5.5, i_typ=0.0000015, i_peak=0.001500,
            interfaces=("I2C",), role="peripheral", pins_required=2,
            package="DFN-8", temp_min=-40, temp_max=125,
            unit_price=3.04, stock=12400, lead_time_days=0,
            raw={"Voltage - Supply": "2.15V ~ 5.5V", "Current - Supply (Max)": "1.5mA",
                 "Interface": "I2C", "Stock": "12400"},
            provenance={"vmin": "Voltage - Supply", "vmax": "Voltage - Supply",
                        "i_peak": "Current - Supply (Max)", "interfaces": "Interface",
                        "stock": "Stock"},
        ),
    ],
    "display": [
        _p(
            mpn="ER-OLED013-1", manufacturer="EastRising",
            description='1.3" 128x64 OLED display module, I2C/SPI', category="Display Module",
            vmin=3.0, vmax=5.5, i_typ=0.020, i_peak=0.040,
            interfaces=("I2C", "SPI"), role="peripheral", pins_required=2,
            package="Module", temp_min=-40, temp_max=70,
            unit_price=6.42, stock=3100, lead_time_days=0,
            raw={"Voltage - Supply": "3.0V ~ 5.5V", "Current - Supply (Max)": "40mA",
                 "Interface": "I2C, SPI", "Stock": "3100"},
            provenance={"vmin": "Voltage - Supply", "vmax": "Voltage - Supply",
                        "i_peak": "Current - Supply (Max)", "interfaces": "Interface",
                        "stock": "Stock"},
        ),
    ],
}


def first(slot: str) -> PartSpec | None:
    options = CATALOGUE.get(slot)
    return options[0] if options else None


def next_after(slot: str, mpn: str) -> PartSpec | None:
    """The next candidate for a slot. Stands in for a re-search under a constraint."""
    options = CATALOGUE.get(slot, [])
    for index, part in enumerate(options):
        if part.mpn == mpn:
            return options[index + 1] if index + 1 < len(options) else None
    return None
