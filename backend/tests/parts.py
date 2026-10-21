"""Part fixtures for the engine tests.

Values and `raw` payloads are shaped like real JLCPCB parameter blocks — free-text
values under a controlled vocabulary — because `cite()` reads through `provenance`
into `raw`, and a test with a tidy synthetic payload would not exercise that path.

These are test doubles, not the demo fixtures. Recorded distributor responses for the
demo live in `fixtures/` and are captured against the live API.
"""

from __future__ import annotations

from continuity.engine.models import PartSpec

DATASHEET = "https://datasheet.lcsc.com/example.pdf"


def _spec(overrides: dict, **defaults) -> PartSpec:
    """Defaults, then whatever the test wanted changed. Lets a test name one field."""
    return PartSpec(**{**defaults, **overrides})


def esp32s3(**overrides) -> PartSpec:
    """WiFi + BLE module. The controller in every test board here."""
    return _spec(overrides,
        mpn="ESP32-S3-WROOM-1-N8R2",
        manufacturer="Espressif Systems",
        description="WiFi + BLE 5.0 module, 8MB flash, 2MB PSRAM",
        category="RF Module",
        vmin=3.0,
        vmax=3.6,
        i_typ=0.100,
        i_peak=0.500,
        interfaces=("I2C", "SPI", "UART"),
        role="master",
        pins_available=36,
        package="Module",
        temp_min=-40,
        temp_max=85,
        unit_price=2.89,
        stock=18432,
        distributor="JLCPCB",
        lifecycle="active",
        lead_time_days=0,
        datasheet=DATASHEET,
        raw={
            "Voltage - Supply": "3.0V ~ 3.6V",
            "Current - Supply (Max)": "500mA",
            "Interface": "I2C, SPI, UART",
            "Number of I/O": "36",
            "Operating Temperature": "-40°C ~ 85°C",
            "Stock": "18432",
            "Lifecycle": "Active",
        },
        provenance={
            "vmin": "Voltage - Supply",
            "vmax": "Voltage - Supply",
            "i_peak": "Current - Supply (Max)",
            "interfaces": "Interface",
            "pins_available": "Number of I/O",
            "temp_max": "Operating Temperature",
            "stock": "Stock",
            "lifecycle": "Lifecycle",
        },
    )


def ap2112k(**overrides) -> PartSpec:
    """600 mA LDO in SOT-23-5 — the part the demo outgrows twice."""
    return _spec(overrides,
        mpn="AP2112K-3.3TRG1",
        manufacturer="Diodes Incorporated",
        description="600mA low-dropout linear regulator, fixed 3.3V",
        category="LDO Regulator",
        vmin=2.5,
        vmax=6.0,
        vout_min=3.3,
        vout_max=3.3,
        i_max=0.600,
        role="passive",
        package="SOT-23-5",
        topology="ldo",
        temp_min=-40,
        temp_max=125,
        unit_price=0.12,
        stock=45210,
        distributor="JLCPCB",
        lifecycle="active",
        lead_time_days=0,
        datasheet=DATASHEET,
        raw={
            "Voltage - Input": "2.5V ~ 6.0V",
            "Voltage - Output": "3.3V",
            "Current - Output": "600mA",
            "Package / Case": "SOT-23-5",
            "Operating Temperature": "-40°C ~ 125°C",
            "Stock": "45210",
        },
        provenance={
            "vmin": "Voltage - Input",
            "vmax": "Voltage - Input",
            "vout_min": "Voltage - Output",
            "vout_max": "Voltage - Output",
            "i_max": "Current - Output",
            "package": "Package / Case",
            "temp_max": "Operating Temperature",
            "stock": "Stock",
        },
    )


def ldo_600ma(**overrides) -> PartSpec:
    """600 mA LDO in SOT-223 — the demo's opening regulator.

    Same silicon class as `ap2112k`, larger package. SOT-223 sheds roughly four times
    the heat of SOT-23-5 (62 vs 250 °C/W), which is what lets the opening board pass
    thermal while still being tight on current.
    """
    return ap2112k(
        **{
            "mpn": "AP2114H-3.3TRG1",
            "description": "600mA low-dropout linear regulator, fixed 3.3V",
            "package": "SOT-223",
            "unit_price": 0.16,
            "stock": 31500,
            "raw": {
                "Voltage - Input": "2.5V ~ 6.0V",
                "Voltage - Output": "3.3V",
                "Current - Output": "600mA",
                "Package / Case": "SOT-223",
                "Operating Temperature": "-40°C ~ 125°C",
                "Stock": "31500",
            },
            **overrides,
        }
    )


def ldo_1a(**overrides) -> PartSpec:
    """1 A LDO in SOT-23-5 — clears the current budget, and is exactly why that is not enough.

    The repair that fixes beat 6 trades package for current rating: more amps, a
    quarter of the thermal path. Which is how the same node fails twice.
    """
    return ap2112k(
        **{
            "mpn": "AP7361C-33E",
            "manufacturer": "Diodes Incorporated",
            "description": "1A low-dropout linear regulator, fixed 3.3V",
            "i_max": 1.000,
            "unit_price": 0.21,
            "stock": 9800,
            **overrides,
        }
    )


def buck_3v3(**overrides) -> PartSpec:
    """A different *kind* of part. 92% efficient, so it barely warms."""
    return _spec(overrides,
        mpn="TPS62825DMQR",
        manufacturer="Texas Instruments",
        description="1A synchronous step-down converter, fixed 3.3V",
        category="Buck Regulator",
        vmin=2.7,
        vmax=6.0,
        vout_min=3.3,
        vout_max=3.3,
        i_max=1.000,
        role="passive",
        package="VSON-HR-8",
        topology="buck",
        efficiency=0.92,
        temp_min=-40,
        temp_max=125,
        unit_price=0.68,
        stock=8200,
        distributor="JLCPCB",
        lifecycle="active",
        lead_time_days=0,
        datasheet=DATASHEET,
        raw={
            "Voltage - Input": "2.7V ~ 6.0V",
            "Voltage - Output": "3.3V",
            "Current - Output": "1A",
            "Efficiency": "92%",
            "Package / Case": "VSON-HR-8",
            "Operating Temperature": "-40°C ~ 125°C",
            "Stock": "8200",
        },
        provenance={
            "vmin": "Voltage - Input",
            "vmax": "Voltage - Input",
            "vout_min": "Voltage - Output",
            "vout_max": "Voltage - Output",
            "i_max": "Current - Output",
            "efficiency": "Efficiency",
            "package": "Package / Case",
            "temp_max": "Operating Temperature",
            "stock": "Stock",
        },
    )


def sht40(**overrides) -> PartSpec:
    """Temperature + humidity sensor. Out of stock, which is the whole point of beat 3."""
    return _spec(overrides,
        mpn="SHT40-AD1B-R2",
        manufacturer="Sensirion",
        description="Digital temperature and humidity sensor, I2C",
        category="Humidity Sensor",
        vmin=1.08,
        vmax=3.6,
        i_typ=0.0000026,
        i_peak=0.000320,
        interfaces=("I2C",),
        role="peripheral",
        pins_required=2,
        package="DFN-4",
        temp_min=-40,
        temp_max=125,
        unit_price=2.31,
        stock=0,
        distributor="JLCPCB",
        lifecycle="active",
        lead_time_days=56,
        datasheet=DATASHEET,
        raw={
            "Voltage - Supply": "1.08V ~ 3.6V",
            "Current - Supply (Max)": "320uA",
            "Interface": "I2C",
            "Package / Case": "DFN-4",
            "Stock": "0",
            "Lead Time": "56 days",
        },
        provenance={
            "vmin": "Voltage - Supply",
            "vmax": "Voltage - Supply",
            "i_peak": "Current - Supply (Max)",
            "interfaces": "Interface",
            "package": "Package / Case",
            "stock": "Stock",
            "lead_time_days": "Lead Time",
        },
    )


def sht31(**overrides) -> PartSpec:
    """Same bus, same supply range, actually in stock."""
    return sht40(
        **{
            "mpn": "SHT31-DIS-B",
            "description": "Digital temperature and humidity sensor, I2C",
            "vmin": 2.15,
            "vmax": 5.5,
            "i_peak": 0.001500,
            "stock": 12400,
            "lead_time_days": 0,
            "unit_price": 3.04,
            **overrides,
        }
    )


def oled(**overrides) -> PartSpec:
    """The display that pushes the 3V3 rail past its regulator."""
    return _spec(overrides,
        mpn="SSD1306-2.42",
        manufacturer="Solomon Systech",
        description='2.42" 128x64 OLED display module, I2C',
        category="Display Module",
        vmin=3.0,
        vmax=5.5,
        i_typ=0.080,
        i_peak=0.200,
        interfaces=("I2C", "SPI"),
        role="peripheral",
        pins_required=2,
        package="Module",
        temp_min=-40,
        temp_max=70,
        unit_price=6.42,
        stock=3100,
        distributor="JLCPCB",
        lifecycle="active",
        lead_time_days=0,
        datasheet=DATASHEET,
        raw={
            "Voltage - Supply": "3.0V ~ 5.5V",
            "Current - Supply (Max)": "200mA",
            "Interface": "I2C, SPI",
            "Stock": "3100",
        },
        provenance={
            "vmin": "Voltage - Supply",
            "vmax": "Voltage - Supply",
            "i_peak": "Current - Supply (Max)",
            "interfaces": "Interface",
            "stock": "Stock",
        },
    )


def spi_flash(**overrides) -> PartSpec:
    """A second peripheral, for the pin-budget and chip-select tests."""
    return _spec(overrides,
        mpn="W25Q128JVSIQ",
        manufacturer="Winbond",
        description="128Mbit serial NOR flash, SPI",
        category="NOR Flash",
        vmin=2.7,
        vmax=3.6,
        i_typ=0.004,
        i_peak=0.025,
        interfaces=("SPI",),
        role="peripheral",
        pins_required=4,
        package="SOIC-8",
        temp_min=-40,
        temp_max=85,
        unit_price=0.94,
        stock=52000,
        distributor="JLCPCB",
        lifecycle="active",
        lead_time_days=0,
        datasheet=DATASHEET,
        raw={
            "Voltage - Supply": "2.7V ~ 3.6V",
            "Interface": "SPI",
            "Package / Case": "SOIC-8",
            "Stock": "52000",
        },
        provenance={
            "vmin": "Voltage - Supply",
            "vmax": "Voltage - Supply",
            "interfaces": "Interface",
            "package": "Package / Case",
            "stock": "Stock",
        },
    )
