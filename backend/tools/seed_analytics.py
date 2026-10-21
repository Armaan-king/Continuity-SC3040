"""Seed a demonstration account with a corpus of completed runs for DS-16 analytics.

    DATABASE_URL=postgresql:///continuity_dev \\
    CONTINUITY_SEED_PASSWORD=... \\
        .venv/bin/python tools/seed_analytics.py --email demo@continuity.dev

The Quality Plan verifies DS-16 "across a seeded corpus of at least 20 completed runs",
and a new account has none, so the analysis can only be demonstrated over seeded data.

## What is synthetic and what is not

The *events* are synthetic: which rules pass, fail or go unchecked is drawn from a fixed
profile per rule with a fixed seed, so the corpus is identical on every run. Everything
downstream of the events is not: frames have exactly the shape `EventStream` emits, the
findings are produced by feeding those frames through the real `FindingRecorder`, and
everything is written through the same `Store` methods a live run uses. The analysis
therefore reads this corpus by exactly the path it reads a real one.

The profiles are chosen so the demonstration exercises every branch the SRS names: a rule
whose unchecked share is over the 20% limit (pin budget, energy budget), repairs that
held and repairs that did not, conflicts that recur across projects, an obsolete and a
not-recommended part, and abandoned and errored runs that must be excluded.

## Re-running

The account named by `--email` is deleted and recreated, with everything it owns. No
other account is touched.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
import hashlib
import os
import pathlib
import random
import sys
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from continuity.api.memory import FindingRecorder  # noqa: E402
from continuity.api.findings import Finding  # noqa: E402

SEED = 3047
"""Chosen because its corpus withholds exactly one familiar rule (pin budget) and keeps
every other branch populated; `tests/test_seed_analytics.py` pins those properties."""
DISTRIBUTOR = "JLCPCB"

# ── parts ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Part:
    mpn: str
    manufacturer: str
    lifecycle: str | None
    package: str
    price: float


PARTS: dict[str, tuple[Part, ...]] = {
    "mcu": (
        Part("ESP32-S3-WROOM-1-N8", "Espressif", "active", "Module", 3.10),
        Part("STM32F103C8T6", "STMicroelectronics", "active", "LQFP-48", 2.45),
        Part("ATMEGA328P-AU", "Microchip", "active", "TQFP-32", 1.85),
        Part("NRF52832-QFAA-R", "Nordic Semiconductor", "active", "QFN-48", 2.90),
    ),
    "regulator": (
        Part("AMS1117-3.3", "Advanced Monolithic Systems", "active", "SOT-223", 0.08),
        Part("LM1117IMPX-3.3", "Texas Instruments", "nrnd", "SOT-223", 0.62),
        Part("AP2112K-3.3TRG1", "Diodes Inc", "active", "SOT-23-5", 0.12),
    ),
    "buck": (
        Part("TPS54331DR", "Texas Instruments", "active", "SOIC-8", 0.74),
        Part("MP1584EN-LF-Z", "Monolithic Power Systems", "active", "SOIC-8", 0.55),
    ),
    "sensor": (
        Part("SHT31-DIS-B", "Sensirion", "active", "DFN-8", 1.95),
        Part("BME280", "Bosch", "active", "LGA-8", 2.30),
        Part("MPU-6050", "TDK InvenSense", "obsolete", "QFN-24", 1.40),
    ),
    "display": (
        Part("SSD1306-OLED-0.96", "Solomon Systech", "active", "Module", 1.60),
        Part("SH1106-OLED-1.3", "Sino Wealth", "active", "Module", 2.10),
    ),
    "motor_driver": (
        Part("DRV8833PWPR", "Texas Instruments", "active", "HTSSOP-16", 0.95),
        Part("L293DD", "STMicroelectronics", "nrnd", "SO-20", 1.75),
    ),
    "usb_uart": (
        Part("CH340G", "WCH", "active", "SOIC-16", 0.32),
        Part("CP2102N-A02-GQFN24", "Silicon Labs", "active", "QFN-24", 1.20),
    ),
    "charger": (
        Part("TP4056", "NanJing Top Power", None, "SOP-8", 0.06),
        Part("MCP73831T-2ACI/OT", "Microchip", "active", "SOT-23-5", 0.58),
    ),
    "flash": (
        Part("W25Q64JVSSIQ", "Winbond", "active", "SOIC-8", 0.48),
        Part("GD25Q64CSIG", "GigaDevice", "active", "SOIC-8", 0.36),
    ),
}

# ── rule profiles: (pass, warn, fail, unchecked) ──────────────────────────────

PROFILES: dict[str, tuple[float, float, float, float]] = {
    "voltage_overlap": (0.82, 0.06, 0.04, 0.08),
    "interface_role_match": (0.86, 0.06, 0.03, 0.05),
    "pin_budget": (0.45, 0.10, 0.10, 0.35),
    "current_budget": (0.58, 0.16, 0.16, 0.10),
    "thermal_dissipation": (0.50, 0.10, 0.30, 0.10),
    "availability": (0.80, 0.12, 0.06, 0.02),
    "temperature_rating": (0.78, 0.04, 0.04, 0.14),
    "footprint": (0.86, 0.12, 0.02, 0.0),
    "rail_coverage": (0.92, 0.0, 0.0, 0.08),
    "energy_budget": (0.40, 0.20, 0.0, 0.40),
}

RULES_FOR_SLOT: dict[str, tuple[str, ...]] = {
    "mcu": ("voltage_overlap", "pin_budget", "availability", "temperature_rating", "footprint"),
    "regulator": ("voltage_overlap", "current_budget", "thermal_dissipation", "availability", "footprint"),
    "buck": ("voltage_overlap", "current_budget", "thermal_dissipation", "availability"),
    "sensor": ("voltage_overlap", "interface_role_match", "availability", "temperature_rating", "rail_coverage"),
    "display": ("voltage_overlap", "interface_role_match", "availability", "footprint"),
    "motor_driver": ("voltage_overlap", "current_budget", "availability", "temperature_rating"),
    "usb_uart": ("voltage_overlap", "interface_role_match", "availability"),
    "charger": ("voltage_overlap", "energy_budget", "availability", "temperature_rating"),
    "flash": ("voltage_overlap", "interface_role_match", "availability", "rail_coverage"),
}

# What the reviewer reaches for, and how often it holds on re-check.
REPAIRS: dict[str, tuple[tuple[str, float], ...]] = {
    "thermal_dissipation": (("change_topology", 0.9), ("swap", 0.35)),
    "current_budget": (("swap", 0.6), ("change_rail", 0.75)),
    "availability": (("swap", 0.92),),
    "voltage_overlap": (("change_rail", 0.8),),
    "pin_budget": (("swap", 0.7),),
}

SIGNATURES: dict[tuple[str, str], str] = {
    ("thermal_dissipation", "regulator"): "thermal_dissipation|regulator|linear|pkg:SOT|drop:1-3V|load:100-500mA",
    ("thermal_dissipation", "buck"): "thermal_dissipation|regulator|switching|pkg:SOIC|drop:3-8V|load:500mA-2A",
    ("current_budget", "regulator"): "current_budget|regulator|linear|pkg:SOT|drop:1-3V|load:500mA-2A",
    ("current_budget", "motor_driver"): "current_budget|motor_driver|pkg:SSOP",
    ("availability", "sensor"): "availability|sensor|pkg:DFN",
    ("voltage_overlap", "sensor"): "voltage_overlap|sensor|pkg:DFN",
    ("pin_budget", "mcu"): "pin_budget|mcu|pkg:QFP",
}


@dataclass(frozen=True)
class ProjectPlan:
    name: str
    slots: tuple[str, ...]
    briefs: tuple[str, ...]


PROJECTS = (
    ProjectPlan(
        "Weather station",
        ("mcu", "regulator", "sensor", "display", "flash"),
        (
            "Outdoor weather station, ESP32, temperature and humidity, small OLED, USB powered",
            "Weather node logging to flash every minute, 5 V input",
            "Same weather station with a BME280 instead of the SHT31",
            "Weather station revision for an industrial enclosure, -20 to 70 C",
            "Weather station with a 12 V input from a solar controller",
        ),
    ),
    ProjectPlan(
        "Solar data logger",
        ("mcu", "buck", "charger", "sensor", "flash"),
        (
            "Solar powered data logger, Li-ion cell, 30 days runtime",
            "Logger with a 12 V panel input and a buck to 3.3 V",
            "Low power logger, nRF52, wake every 10 minutes",
            "Logger for a greenhouse, humidity and temperature",
            "Logger revision using a cheaper MCU",
        ),
    ),
    ProjectPlan(
        "Motor controller",
        ("mcu", "regulator", "motor_driver", "usb_uart"),
        (
            "Two DC motor controller, 12 V supply, USB serial for tuning",
            "Small robot base, two brushed motors, STM32",
            "Motor controller revision with a higher current driver",
            "Motor controller for a 6 V gearbox motor",
            "Same controller with an AVR for an Arduino-compatible build",
        ),
    ),
    ProjectPlan(
        "BLE wearable",
        ("mcu", "regulator", "charger", "sensor"),
        (
            "Wrist wearable, BLE, IMU, coin-sized Li-po",
            "Wearable revision with a step counter and 7 day battery",
            "Wearable for a classroom study, temperature logging over BLE",
            "Wearable with a smaller regulator package",
            "Wearable for sports, higher IMU rate",
        ),
    ),
    ProjectPlan(
        "USB sensor hub",
        ("mcu", "regulator", "sensor", "usb_uart", "display"),
        (
            "USB sensor hub with three I2C sensors and a status display",
            "Sensor hub with serial output to a laptop",
            "Desk sensor hub for air quality",
            "Sensor hub revision for production of 500 units",
            "Sensor hub with a 5 V wall adaptor",
        ),
    ),
)

ABANDONED = ("Weather station", "Motor controller")
PROJECT_ORDER = {plan.name: index for index, plan in enumerate(PROJECTS)}
ERRORED = ("Solar data logger",)


# ── one run's frames ──────────────────────────────────────────────────────────


@dataclass
class RunPlan:
    thread_id: str
    project: str
    prompt: str
    status: str
    events: list[dict[str, Any]] = field(default_factory=list)
    bom: list[dict[str, Any]] = field(default_factory=list)

    def emit(self, kind: str, **fields: Any) -> dict[str, Any]:
        event = {"v": 1, "seq": len(self.events), "type": kind, "thread_id": self.thread_id, **fields}
        self.events.append(event)
        return event


def _part_frame(part: Part) -> dict[str, Any]:
    return {
        "mpn": part.mpn,
        "manufacturer": part.manufacturer,
        "package": part.package,
        "lifecycle": part.lifecycle,
        "distributor": DISTRIBUTOR,
        "unit_price": part.price,
        "currency": "USD",
    }


def _outcome(rng: random.Random, rule: str) -> str:
    weights = PROFILES[rule]
    return rng.choices(("pass", "warn", "fail", "unchecked"), weights=weights)[0]


def _check(run: RunPlan, slot: str, rule: str, outcome: str) -> None:
    unchecked = outcome == "unchecked"
    detail = {
        "pass": f"{rule.replace('_', ' ')} holds.",
        "warn": f"{rule.replace('_', ' ')} is inside its margin.",
        "fail": f"{rule.replace('_', ' ')} is violated.",
        "unchecked": f"a figure {rule.replace('_', ' ')} needs is unpublished — unchecked.",
    }[outcome]
    run.emit(
        "check",
        slot=slot,
        rule=rule,
        scope=None,
        status="warn" if unchecked else outcome,
        detail=detail,
        unchecked=unchecked,
    )


def _repair(rng: random.Random, run: RunPlan, slot: str, rule: str, part: Part) -> Part:
    """Conflict → repair → reselection → re-check, in the order a live run emits them."""
    signature = SIGNATURES.get((rule, slot))
    conflict = {"rule": rule, "involved": [slot], "edge": None, "message": f"{part.mpn}: {rule} failed.", "evidence": []}
    if signature:
        conflict["signature"] = signature
    run.emit("conflict", **conflict)

    options = REPAIRS.get(rule)
    if not options:
        return part  # left for the engineer: an unresolved finding
    action, holds = options[rng.randrange(len(options))]
    run.emit("repair", slot=slot, action=action, rationale=f"{action.replace('_', ' ')} to clear {rule}.", constraint={}, alternatives=[])
    alternatives = [p for p in PARTS[slot] if p.mpn != part.mpn] or [part]
    replacement = alternatives[rng.randrange(len(alternatives))]
    run.emit("selection", slot=slot, part=_part_frame(replacement), status="pass")

    roll = rng.random()
    if roll < 0.05:
        _check(run, slot, rule, "unchecked")
    elif roll < 0.05 + holds * 0.95:
        _check(run, slot, rule, "pass")
    else:
        _check(run, slot, rule, "fail")
        run.emit("conflict", rule=rule, involved=[slot], edge=None, message=f"{replacement.mpn}: {rule} still fails.", evidence=[], **({"signature": signature} if signature else {}))
    return replacement


def _run(rng: random.Random, plan: ProjectPlan, thread_id: str, prompt: str, status: str) -> RunPlan:
    run = RunPlan(thread_id, plan.name, prompt, status)
    chosen = {slot: PARTS[slot][rng.randrange(len(PARTS[slot]))] for slot in plan.slots}
    for slot, part in chosen.items():
        run.emit("selection", slot=slot, part=_part_frame(part), status="pass")

    resolved = 0
    slots = plan.slots if status == "done" else plan.slots[: max(1, len(plan.slots) // 2)]
    for slot in slots:
        for rule in RULES_FOR_SLOT[slot]:
            outcome = _outcome(rng, rule)
            _check(run, slot, rule, outcome)
            if outcome == "fail":
                before = chosen[slot]
                chosen[slot] = _repair(rng, run, slot, rule, before)
                resolved += chosen[slot] is not before

    if status == "done":
        run.bom = [
            {"slot": slot, "mpn": part.mpn, "manufacturer": part.manufacturer, "qty": 1,
             "unit_price": part.price, "currency": "USD", "distributor": DISTRIBUTOR}
            for slot, part in chosen.items()
        ]
        run.emit("done", summary={"slots": len(chosen), "placed": len(chosen),
                                  "conflicts_resolved": resolved, "elapsed_s": round(rng.uniform(48, 131), 1)})
    elif status == "error":
        run.emit("error", message="The distributor search timed out.", recoverable=True)
    return run


def _thread_id(email: str, project: str, number: int) -> str:
    return hashlib.sha256(f"seed:{email}:{project}:{number}".encode()).hexdigest()[:12]


def generate_corpus(email: str, seed: int = SEED) -> list[RunPlan]:
    """Every run the account will own, in creation order. Pure and deterministic."""
    rng = random.Random(seed)
    runs: list[RunPlan] = []
    for plan in PROJECTS:
        for number, brief in enumerate(plan.briefs):
            runs.append(_run(rng, plan, _thread_id(email, plan.name, number), brief, "done"))
        extra = len(plan.briefs)
        if plan.name in ABANDONED:
            runs.append(_run(rng, plan, _thread_id(email, plan.name, extra), f"{plan.briefs[0]} (stopped)", "abandoned"))
        if plan.name in ERRORED:
            runs.append(_run(rng, plan, _thread_id(email, plan.name, extra + 1), f"{plan.briefs[1]} (retry)", "error"))
    # Generated in a fixed order so the figures never move, but written with the excluded
    # runs first: the projects page shows each project's newest run, and a project whose
    # newest run is a stopped one reads as broken in a demonstration.
    return sorted(runs, key=lambda run: (PROJECT_ORDER[run.project], run.status == "done"))


def findings_for(run: RunPlan) -> list[Finding]:
    """What the live pipeline would have recorded for these frames — by the same code."""
    recorder = FindingRecorder()
    for event in run.events:
        recorder.feed(event)
    return recorder.findings()


# ── writing it ────────────────────────────────────────────────────────────────


async def write_corpus(store: Any, email: str, password_hash: str, runs: list[RunPlan]) -> str:
    async with store.pool.connection() as conn:
        await conn.execute("DELETE FROM users WHERE email = %s", (email.strip().lower(),))
    user = await store.create_user(email, password_hash)
    await store.mark_onboarded(user.id)

    projects: dict[str, str] = {}
    for run in runs:
        if run.project not in projects:
            projects[run.project] = (await store.create_project(user.id, run.project)).id
        await store.create_thread(run.thread_id, projects[run.project], user.id, run.prompt)
        await store.save_run_events(run.thread_id, run.events)
        if run.status == "done":
            await store.save_findings(run.thread_id, findings_for(run))
            await store.save_bom(run.thread_id, run.bom)
            await store.save_summary(run.thread_id, run.events[-1]["summary"])
        await store.save_progress(run.thread_id, run.events[-1]["seq"], run.status)
    return user.id


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", default="demo@continuity.dev")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL")
    password = os.environ.get("CONTINUITY_SEED_PASSWORD")
    if not url or not password:
        sys.exit("Set DATABASE_URL and CONTINUITY_SEED_PASSWORD (the demo account's password).")

    from psycopg_pool import AsyncConnectionPool

    from continuity.api.auth import hasher
    from continuity.api.store import Store

    runs = generate_corpus(args.email, args.seed)
    async with AsyncConnectionPool(url, min_size=1, max_size=2, open=False, kwargs={"autocommit": True}) as pool:
        await pool.open()
        store = Store(pool)
        await store.setup()
        await write_corpus(store, args.email, hasher.hash(password), runs)

    done = sum(run.status == "done" for run in runs)
    print(f"Seeded {args.email}: {done} completed runs, {len(runs) - done} excluded, {len(PROJECTS)} projects.")


if __name__ == "__main__":
    asyncio.run(main())
