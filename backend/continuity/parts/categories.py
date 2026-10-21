"""What kind of part a slot wants, in a vocabulary the engine owns.

The peripheral equivalent of `Topology`. A regulator slot carries
`{topology: buck, vin_min: 48, vout: 3.3}` and returns six correct parts; the same slot
searched as plain text returns one screw terminal. Peripheral slots carried a string and
nothing else, and a distributor's free-text search is not a specification language.

Measured live, 10 Aug:

    'environmental sensor' → 0 results, so `search` fell back to its own first word
    'environmental'        → Circuit Protection / Fuseholders
                             Connectors / Female Headers
                             Hardware Fasteners / Metal Products SMT Copper Sheet

"Environmentally" matched RoHS marketing copy. Every one of those rows already carried
the distributor's own `category`, so nothing had to be fetched to know they were not
sensors — the slot simply had no way to say *I am a sensor, not a fuse clip*.

## Coarse on purpose, and filtered locally rather than pushed down

`jlc_search` takes `subcategory_name`, and it genuinely filters — ten of ten hits stayed
inside "Temperature Sensors" when it was set, and an unknown name comes back as a loud
`Subcategory not found` rather than being silently ignored the way an unknown spec filter
is. It is still the wrong lever here, for two reasons measured on the same probe:

- It matches *subcategories*, fuzzily. Passing the category "Sensors" resolved to
  "VOC Sensors" and returned four parts — a silent, arbitrary narrowing.
- Pinning a subcategory alongside the planner's text zeroes out. `'environmental'` inside
  "Temperature and Humidity Sensor" returns nothing, because no sensor says
  "environmental" either.

JLCPCB publishes no filter at the *category* level. So the constraint stays coarse, the
pool is fetched deep (`sourcing.DEEP_POOL`), and the wrong categories are dropped from
the results — which costs nothing, because every row already states its own.

## One name may satisfy several distributor categories

Current sensors are filed under `Magnetic Sensors`, not `Sensors`; motor drivers appear
under both `Motor Driver ICs` and `Power Management (PMIC)`. Both are observed in
recorded payloads. The mapping is one-to-many for exactly that reason, and it is checked
against the server's published list by a live test rather than against a memory of it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    """One name a planner may use, and what satisfies it at the distributor."""

    accepts: frozenset[str]
    """JLCPCB top-level category names. Verbatim — a near-miss filters everything out."""

    hint: str
    """Shown to the planner. What a person would call the parts in this group."""

    subcategories: tuple[str, ...] = ()
    """Verified JLCPCB subcategories to use for one thin-shortlist rescue search."""

    rescue_unmatched: bool = False
    """May a rescue fall back to the first shelf when the query matches none of them?

    True only where the shelves are interchangeable kinds of the same thing: any sensor
    shelf beats the fuse clip that "environmental sensor" actually returned, so landing on
    the wrong sensor shelf still lands on a sensor.

    False everywhere else, and that is the important half. `radio` lists WiFi, Bluetooth,
    LoRa, GNSS and RF Transceiver shelves, which are *mutually exclusive kinds of radio* —
    there, declaration order is not a neutral tie-break, it is a wrong answer. It put an
    ESP32 in a GPS slot on 13 Aug, and a `CR1220` coin cell in a `connector` slot before
    that (see the note on `connector`, where it was fixed by reordering the list — which
    only ever works for one wording of one query).
    """

    defining_shelves: bool = False
    """Are these shelves what the category *means*, rather than a search aid?

    When true, a candidate filed on another shelf is dropped — but only while on-shelf
    candidates remain, so this can never empty a shortlist and turn a wrong part into no
    part at all.
    """


def _category(
    hint: str,
    *accepts: str,
    subcategories: tuple[str, ...] = (),
    rescue_unmatched: bool = False,
    defining_shelves: bool = False,
) -> Category:
    return Category(
        accepts=frozenset(accepts),
        hint=hint,
        subcategories=subcategories,
        rescue_unmatched=rescue_unmatched,
        defining_shelves=defining_shelves,
    )


SHELF_SYNONYMS = {"gps": "gnss", "glonass": "gnss", "beidou": "gnss"}
"""Query words that name a shelf the distributor calls something else.

Deliberately tiny, and every entry earns its place with a measured failure. `GPS` is the
whole of it so far: JLCPCB files receivers under **GNSS Modules**, nobody writing a brief
says GNSS, and with no shared word the rescue scored every radio shelf zero and took the
first — WiFi.
"""


CATEGORIES: dict[str, Category] = {
    "mcu": _category(
        # A WiFi/BLE module is the microcontroller on the boards people ask us for: it
        # runs the firmware, it is the bus master, and `role: master` is what R2 and R3
        # key on. JLCPCB files it under IoT/Communication Modules, so accepting only
        # `Embedded Processors & Controllers` discarded *every* module.
        #
        # Measured 12 Aug, query "ESP32 module", 20 candidates: the `mcu` filter kept
        # **zero**, the rescue search then supplied bare chips, and bare chips publish no
        # supply current at all — so R4 and R5 had nothing to work with on the demo board.
        # The same 20 candidates as a `radio` slot keep 20, of which 19 state a transmit
        # current. This vocabulary describes what a part *does*, not which shelf a
        # distributor files it on.
        "microcontrollers, MPUs, FPGAs, DSPs, and WiFi/BLE modules that run the firmware",
        "Embedded Processors & Controllers",
        "IoT/Communication Modules",
        "RF and Wireless",
        subcategories=("Microcontrollers (MCU/MPU/SOC)", "WiFi Modules", "Bluetooth Modules"),
    ),
    "regulator": _category(
        # "chargers" was in this hint until 13 Aug, and a charger slot classified as
        # `regulator` is one a DC-DC converter satisfies by definition — which is how a
        # solar board's charge controller became a TPS631000 buck-boost with every
        # electrical check passing. A charger is a different kind of part; it now says so.
        "regulators, PMICs, PoE controllers — NOT battery chargers, see `charger`",
        "Power Management (PMIC)",
        "Power Modules",
        subcategories=(
            "DC-DC Converters",
            "Voltage Regulators - Linear, Low Drop Out (LDO) Regulators",
        ),
    ),
    "poe": _category(
        # Verified live, 13 Aug: this query on its own text returns TPS23754PWPR,
        # TPS23753APWR, TPS2378DDAR, IP804AR, TME7624 and TMI7303B, every one filed on
        # the shelf named below. A PoE powered-device controller negotiates and takes the
        # 48 V; the converter behind it is a separate slot and a separate kind of part.
        "PoE powered-device controllers — the 802.3af/at front end, not the converter",
        "Power Management (PMIC)",
        subcategories=("Power Over Ethernet (PoE) Controllers",),
        defining_shelves=True,
    ),
    "charger": _category(
        "battery charger and charge-controller ICs, solar and USB",
        # The same top-level shelves `regulator` accepts, deliberately: narrowing the pool
        # would be a new way to fail. The discrimination happens one level down, on the
        # subcategory, which is where a charger and a converter actually differ.
        "Power Management (PMIC)",
        "Power Modules",
        subcategories=("Battery Management",),
        defining_shelves=True,
    ),
    "sensor": _category(
        "temperature, humidity, pressure, motion, light, gas, image, current",
        "Sensors",
        "Magnetic Sensors",
        subcategories=(
            "Temperature and Humidity Sensor",
            "Temperature Sensors",
            "Sensor Modules",
            "Pressure Sensors",
            "Accelerometers",
            "Ambient Light Sensors",
        ),
        # The motivating case: "environmental sensor" shares no word with any shelf here,
        # and every one of them is still a sensor. This is the only category where falling
        # back to the first shelf is a defensible answer rather than a coin toss.
        rescue_unmatched=True,
    ),
    "display": _category(
        "OLED, LCD, segment and dot-matrix displays", "Displays",
        subcategories=("OLED Display", "LCD Screen", "LED Segment Displays"),
    ),
    "radio": _category(
        "WiFi, BLE, LoRa, GNSS, cellular, RF transceivers",
        "IoT/Communication Modules",
        "RF and Wireless",
        subcategories=(
            "WiFi Modules",
            "Bluetooth Modules",
            "LoRa Modules",
            "GNSS Modules",
            "RF Transceiver ICs",
        ),
    ),
    "antenna": _category(
        "antennas, baluns, RF front ends", "RF and Wireless", subcategories=("Antennas",)
    ),
    "memory": _category(
        "flash, EEPROM, SRAM, FRAM", "Memory", subcategories=("NOR FLASH", "EEPROM", "SRAM")
    ),
    "interface": _category(
        "CAN, RS-485, RS-232, USB, Ethernet, I/O expanders, UARTs", "Interface",
        subcategories=(
            "CAN Transceivers",
            "RS-485 / RS-422 ICs",
            "USB Converters",
            "Ethernet Transceivers",
            "I/O Expanders",
            "UART",
        ),
    ),
    "motor_driver": _category(
        # `defining_shelves` since 13 Aug. JLCPCB files `TC7S08FU(TE85L,F)` — a Toshiba
        # 2-input AND gate in a 5-pin SOT-353 — under `Motor Driver ICs` with an empty
        # `specs` and the description "SOT-353 Motor Driver ICs ROHS". Accepted category,
        # plausible shelf, and nothing in the payload to contradict it, so a stepper board
        # placed four of them and passed every check. A driver lives on one of the four
        # shelves below or it is not a driver; the parent shelf is where the mislabelled go.
        "brushed, brushless, stepper and gate drivers",
        "Motor Driver ICs",
        "Power Management (PMIC)",
        subcategories=(
            "Brushless DC (BLDC) Motor Driver",
            "Brushed DC Motor Drivers",
            "Stepper Motor Driver",
            "Gate Drivers",
        ),
        defining_shelves=True,
    ),
    "converter": _category(
        "ADCs, DACs, analog front ends", "Data Acquisition",
        subcategories=(
            "Analog to Digital Converters (ADC)",
            "Digital to Analog Converters (DAC)",
            "Analog Front End (AFE)",
        ),
    ),
    "rtc": _category(
        # Split from `clock` on 13 Aug. A slot labelled "Real-Time Clock" was searched with
        # the query "RTC crystal oscillator" — which names a crystal — and JLCPCB returned
        # six crystal oscillators, every one of which passed `clock`'s category filter
        # because that entry accepts the crystal shelf too. A timekeeping IC and a
        # frequency source are not the same kind of part.
        "real-time clock and calendar ICs that keep the time",
        "Clock/Timing",
        subcategories=("Real Time Clocks",),
        defining_shelves=True,
    ),
    "clock": _category(
        "oscillators, crystals and resonators — a frequency source, not a clock IC",
        "Clock/Timing",
        "Crystals, Oscillators, Resonators",
        subcategories=("Crystal Oscillators", "Crystals", "Ceramic Resonators"),
    ),
    "amplifier": _category("op-amps, comparators, instrumentation amps", "Amplifiers/Comparators"),
    "audio": _category(
        "codecs, microphones, speakers, buzzers",
        "Audio Products / Vibration Motors",
        "Interface",
        subcategories=("Audio Interface ICs", "MEMS Microphones", "Buzzers"),
    ),
    "led": _category(
        "indicator LEDs, optocouplers, LED drivers", "Optoelectronics", "LED Drivers",
        subcategories=("LED Indication - Discrete",),
    ),
    "connector": _category(
        "headers, sockets, card slots, terminals, battery holders",
        "Connectors",
        "Terminal",
        "Industrial control electrical",
        # `Battery connector` is where JLCPCB actually files holders — CR2032, 18650, the
        # lot — under the `Industrial control electrical` category this entry already
        # accepts. Without it in the rescue list, a thin "Li-ion battery holder" shortlist
        # widened to **Pin Headers**, the first name declared, and the demo board was given
        # a `CR1220` coin cell. The category filter was never the problem; the rescue had
        # nowhere sensible to widen to. Verified against `jlc_search_help`, 12 Aug.
        subcategories=(
            "Battery connector",
            "Button And Strip Battery Connector",
            "Pin Headers",
            "Female Headers",
            "SD Card / Memory Card Connector",
        ),
    ),
    "protection": _category(
        "fuses, TVS and ESD diodes, MOSFETs, load switches",
        "Circuit Protection",
        "Diodes",
        "Transistors/Thyristors",
        subcategories=("Fuseholders",),
    ),
    "passive": _category(
        "resistors, capacitors, inductors",
        "Resistors",
        "Capacitors",
        "Inductors, Coils, Chokes",
    ),
}
"""Every kind of part these boards are made of. Unknown names are dropped, not guessed."""


def satisfies(name: object, stated: object) -> bool | None:
    """Does a candidate's own category satisfy the one the slot asked for?

    `None` means undecidable, and undecidable keeps the part — the same direction as
    `payload.accepts_input`. Two ways to land there: a name outside our vocabulary, which
    is a constraint we never promised to enforce, and a row that states no category at
    all, which is a gap in the payload rather than evidence of a wrong part.
    """
    spec = CATEGORIES.get(str(name or "").strip().lower())
    if spec is None:
        return None
    published = str(stated or "").strip()
    if not published:
        return None
    return published in spec.accepts


def canonical(stated: object) -> str | None:
    """Our own name for a distributor category, or `None` if it is not one we know.

    The reverse of `satisfies`. It exists so that anything keyed on "what kind of part is
    this" uses one vocabulary rather than the distributor's: JLCPCB's own wording is a
    product decision at their end, and a re-titled category would silently stop matching
    a key built from it while every test still passed.
    """
    published = str(stated or "").strip()
    if not published:
        return None
    # Already ours. The offline catalogue states our own names rather than JLCPCB's, so
    # without this the fixtures and live data disagree about the same part: the recorded
    # walkthrough produced `thermal_dissipation|linear|pkg:SOT|…` with no category while a
    # live board produced `…|regulator|linear|…`. Signatures match exactly, so those two
    # could never meet, and the walkthrough's precedents would never fire on a real board.
    if published.casefold() in CATEGORIES:
        return published.casefold()
    # A listing states its *subcategory* — an AMS1117 arrives as "Voltage Regulators -
    # Linear, Low Drop Out (LDO) Regulators", never as the "Power Management (PMIC)" top
    # level. Both are checked, top level first, because only the subcategory lists are
    # narrow enough to be unambiguous and only the top level is guaranteed present.
    for source in (lambda spec: spec.accepts, lambda spec: spec.subcategories):
        match = next(
            (name for name, spec in CATEGORIES.items() if published in source(spec)), None
        )
        if match is not None:
            return match
    return None


def prompt_lines() -> str:
    """The vocabulary, formatted for the planner prompt.

    Generated rather than typed twice. A name offered in the prompt with no entry in the
    table would be a constraint the system accepts and then silently ignores, which is
    the precise failure `_push_down` documents for unrecognised spec filters.
    """
    width = max(len(name) for name in CATEGORIES)
    return "\n".join(
        f"             {name.ljust(width)}  {spec.hint}" for name, spec in CATEGORIES.items()
    )
