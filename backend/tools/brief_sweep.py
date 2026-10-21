"""Run a spread of unfamiliar briefs end to end and report what each board came out as.

    ../.venv/bin/python tools/brief_sweep.py                  # all of them
    ../.venv/bin/python tools/brief_sweep.py --only motor can # substring filter
    ../.venv/bin/python tools/brief_sweep.py --json out.json  # keep the raw events

## Why this exists

Four briefs have been verified by hand, and every unfamiliar brief run so far has found a
defect — a PoE controller sourced as an 80 V buck, a GPS slot filled with WiFi modules, a
stale verdict left standing as truth. Those were all found by running a board nobody had
run before and reading every line, which does not scale and does not repeat.

This drives the same HTTP route the browser drives, against live search and a live model,
and applies the same "would be a bug" checks to every run.

## What it can and cannot decide

It reports two kinds of thing, and the difference matters.

**Failures** are things that are wrong no matter what the brief said: a slot that was
planned and never placed, a conflict with no evidence behind it, an edge from a node to
itself, a run that ends with a rule still failing.

**Flags** are things that are usually wrong and occasionally right — a boost converter on
a supply above the rail it feeds, a search query long enough to be a sentence, a part
whose category does not match the slot that asked for it. A human reads those. The
harness deliberately does not fail on them, because a sweep that cries wolf gets ignored,
and being ignored is worse than not existing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

API = "http://localhost:8000"
ACCOUNT = ("brief-sweep@example.com", "brief-sweep-2026")

# Chosen for spread, not for difficulty: motor control, audio, industrial serial, power
# negotiation, ultra-low-power display, automotive, instrumentation, RF, motion, analogue.
# Each names its own supply so the run does not stop to ask, except where asking is the
# behaviour under test.
BRIEFS: list[tuple[str, str]] = [
    ("motor", "brushless motor driver, 12V input, CAN bus, current sensing"),
    ("audio", "USB audio interface with a codec and a headphone amplifier"),
    ("rs485", "RS-485 industrial sensor node, 24V rail, runs outdoors year round"),
    ("usbpd", "USB-C PD trigger board that negotiates 20V, with a status LED"),
    ("epaper", "e-paper badge on a coin cell with BLE, must last a year"),
    ("can", "CAN gateway bridging two buses, 12V automotive supply"),
    ("thermo", "4 channel thermocouple logger with an SD card, 5V USB powered"),
    ("lora", "LoRa GPS tracker, solar powered, runs outdoors year round"),
    ("stepper", "stepper motor controller for a 3D printer, 24V, four axes"),
    ("current", "high side current sense board, 48V bus, isolated I2C output"),
]

QUERY = re.compile(r'searching JLCPCB for [“"]([^”"]+)[”"]', re.I)


@dataclass
class Run:
    name: str
    brief: str
    events: list[dict[str, Any]] = field(default_factory=list)
    elapsed: float = 0.0
    transport_error: str | None = None

    def of(self, type_: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("type") == type_]

    @property
    def summary(self) -> dict[str, Any]:
        done = self.of("done")
        return done[-1].get("summary", {}) if done else {}

    @property
    def queries(self) -> list[str]:
        found = []
        for event in self.of("reasoning"):
            match = QUERY.search(event.get("text") or "")
            if match:
                found.append(match.group(1))
        return found

    @property
    def selections(self) -> dict[str, dict[str, Any]]:
        """Last selection per slot — a repaired slot is represented by its replacement."""
        chosen: dict[str, dict[str, Any]] = {}
        for event in self.of("selection"):
            chosen[event["slot"]] = event["part"]
        return chosen

    @property
    def final_checks(self) -> dict[tuple[str, str, str | None], dict[str, Any]]:
        """Last verdict per (rule, slot, scope). Earlier ones were superseded."""
        latest: dict[tuple[str, str, str | None], dict[str, Any]] = {}
        for event in self.of("check"):
            latest[(event["rule"], event["slot"], event.get("scope"))] = event
        return latest


# ── the checks ────────────────────────────────────────────────────────────────


def acted_on(run: Run, check: dict[str, Any]) -> bool:
    """Did the board change after this verdict was emitted?

    The engine never restates a verdict that stops applying. When a repair removes the
    second I2C master, `_bus_contention` finds one holder, hits `len(holders) < 2` and
    emits *nothing* — so the last word on the wire for that key stays `fail` even though
    the final board is sound. Anything that judges on the last verdict alone therefore
    marks every repaired board broken.
    """
    after = check.get("seq", -1)
    return any(
        event.get("seq", -1) > after and event.get("type") in ("repair", "selection")
        for event in run.events
    )


def explained_losses(run: Run) -> list[str]:
    """Checks that name a slot the search could not fill, and the query it tried."""
    return [
        event["detail"]
        for event in run.of("check")
        if "no part found" in (event.get("detail") or "").lower()
    ]


def unresolved(run: Run) -> list[tuple[str, str, str]]:
    """Failing verdicts that nothing subsequently acted on."""
    return [
        (key[0], key[1], event.get("detail") or "")
        for key, event in run.final_checks.items()
        if event.get("status") == "fail" and not acted_on(run, event)
    ]


def repaired_but_unrestated(run: Run) -> list[tuple[str, str, str]]:
    """Failing verdicts a repair did act on, which no later verdict ever replaced."""
    return [
        (key[0], key[1], event.get("detail") or "")
        for key, event in run.final_checks.items()
        if event.get("status") == "fail" and acted_on(run, event)
    ]


def failures(run: Run) -> list[str]:
    """Wrong regardless of what the brief asked for."""
    out: list[str] = []

    if run.transport_error:
        return [f"run did not complete: {run.transport_error}"]

    for event in run.of("error"):
        out.append(f"error event: {event.get('message')}")

    # A run that stops on a question has not failed — `escalate` calls `interrupt()` and
    # waits, which is the design. This harness deliberately does not answer: the first
    # suggestion on an escalation is "Accept the ...", and a sweep that waives faults to
    # keep runs moving reports clean boards over broken ones. It did exactly that on
    # 13 Aug, and the resulting question-then-done ordering was briefly mistaken for a
    # defect in the product.
    if not run.of("done") and not run.of("question"):
        out.append("stream ended with no `done` event and no question")

    # A lost slot is only a defect when it is lost *quietly*. The engine emits a check
    # naming the slot and the query it tried — "No part found for Coin Cell Holder —
    # searched JLCPCB for 'coin cell holder' among connector parts" — and a slot dropped
    # that loudly is the honesty rule working, not failing. Only an unexplained loss is a
    # failure; the explained kind is a flag, because the search term is usually the bug.
    planned = run.summary.get("slots")
    placed = run.summary.get("placed")
    if planned is not None and placed is not None and placed < planned:
        lost = planned - placed
        if not explained_losses(run):
            out.append(f"{lost} of {planned} slots planned but never placed, with no explanation")

    for event in run.of("conflict"):
        if not event.get("evidence"):
            out.append(f"conflict on {event.get('rule')} carries no evidence")

    for event in run.of("slot_added"):
        for edge in event.get("edges") or []:
            if edge.get("from") and edge.get("from") == edge.get("to"):
                out.append(f"edge {edge.get('id')} runs from {edge.get('from')} to itself")

    # A run that ends while a rule still reads fail *and nothing was done about it* is the
    # headline bug this project exists to prevent.
    if run.of("done"):
        for rule, slot, detail in unresolved(run):
            out.append(f"finished with a failing check, nothing repaired — {rule} on {slot}: {detail}")

    if run.of("bom"):
        rows = {row["mpn"] for row in run.of("bom")[-1].get("rows", [])}
        selected = {part["mpn"] for part in run.selections.values()}
        drifted = selected - rows
        if drifted:
            # The fastest way to spot a stale repair: the graph kept a part the BOM
            # dropped, or the reverse.
            out.append(f"BOM and graph disagree — in the graph only: {sorted(drifted)}")

    return out


def flags(run: Run) -> list[str]:
    """Usually wrong, occasionally right. A person decides."""
    out: list[str] = []

    for query in run.queries:
        if len(query.split()) > 4:
            out.append(f"search query reads like a sentence: {query!r}")

    for slot, part in run.selections.items():
        category = (part.get("category") or "").lower()
        topology = (part.get("topology") or "").lower()
        if not category:
            out.append(f"{slot}: {part['mpn']} has no category")
        if "regulator" in slot.lower() or category in ("regulator", "converter"):
            vmin, vout = part.get("vmin"), part.get("vout")
            if topology == "boost" and vmin and vout and vmin > vout:
                out.append(
                    f"{slot}: boost {part['mpn']} but input {vmin} V already exceeds "
                    f"output {vout} V — a buck belongs here"
                )

    unchecked = [
        f"{key[0]} on {key[1]}"
        for key, event in run.final_checks.items()
        if event.get("status") in ("warn", "unchecked")
    ]
    if len(unchecked) > 6:
        out.append(f"{len(unchecked)} checks ended warn/unchecked — thin data on this board")

    if run.of("question"):
        out.append(f"paused for the user, unanswered: {run.of('question')[-1].get('text')!r}")

    for rule, slot, detail in repaired_but_unrestated(run):
        out.append(f"repaired but never restated — {rule} on {slot}: {detail}")

    for detail in explained_losses(run):
        out.append(f"slot dropped, and said so — {detail}")

    # The planner can under-plan a board so thoroughly that every check passes for want of
    # anything to check: a USB-C PD trigger board planned as an MCU and an LED reports a
    # clean 2/2. A floor cannot know what the brief needed, but it can notice a board too
    # small to be one.
    planned = run.summary.get("slots") or 0
    if planned and planned < 3:
        out.append(f"only {planned} slots planned — too few to be the board the brief describes")

    return out


# ── driving a run ─────────────────────────────────────────────────────────────


async def sign_in(http: httpx.AsyncClient) -> None:
    email, password = ACCOUNT
    body = {"email": email, "password": password}
    made = await http.post("/auth/register", json=body)
    if made.status_code not in (201, 409, 400):
        made.raise_for_status()
    if made.status_code != 201:
        (await http.post("/auth/login", json=body)).raise_for_status()


async def stream(http: httpx.AsyncClient, path: str, body: dict[str, Any], run: Run) -> None:
    async with http.stream("POST", path, json=body) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                run.events.append(json.loads(line[6:]))


async def execute(http: httpx.AsyncClient, name: str, brief: str) -> Run:
    run = Run(name=name, brief=brief)
    started = time.monotonic()
    try:
        await stream(http, "/design", {"prompt": brief}, run)
    except Exception as error:  # noqa: BLE001 — the report is the product here
        run.transport_error = f"{type(error).__name__}: {error}"
    run.elapsed = time.monotonic() - started
    return run


def report(run: Run) -> bool:
    bad, warn = failures(run), flags(run)
    mark = "FAIL" if bad else ("flag" if warn else "ok")
    summary = run.summary
    print(f"\n[{mark}] {run.name} — {run.brief}")
    print(
        f"    {summary.get('placed', '?')}/{summary.get('slots', '?')} slots placed · "
        f"{summary.get('conflicts_resolved', 0)} conflicts resolved · {run.elapsed:.0f}s"
    )
    for slot, part in run.selections.items():
        bits = [part.get("category") or "?", part.get("topology") or ""]
        print(f"      {slot:<22} {part['mpn']:<26} {' '.join(b for b in bits if b)}")
    for line in bad:
        print(f"    FAIL  {line}")
    for line in warn:
        print(f"    flag  {line}")
    return not bad


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None, help="substring filter on the name")
    parser.add_argument("--json", type=pathlib.Path, default=None, help="write raw events here")
    args = parser.parse_args()

    chosen = [
        (name, brief)
        for name, brief in BRIEFS
        if not args.only or any(term.lower() in name.lower() for term in args.only)
    ]
    if not chosen:
        raise SystemExit("no briefs matched --only")

    print(f"{len(chosen)} briefs against {API}")
    runs: list[Run] = []
    clean = 0

    async with httpx.AsyncClient(base_url=API, timeout=httpx.Timeout(600.0)) as http:
        health = await http.get("/health")
        health.raise_for_status()
        await sign_in(http)

        for index, (name, brief) in enumerate(chosen, 1):
            print(f"\n({index}/{len(chosen)}) {name} …", flush=True)
            run = await execute(http, name, brief)
            runs.append(run)
            clean += report(run)

    print(f"\n{'─' * 78}")
    print(f"{clean}/{len(runs)} briefs clean")
    flagged = [r.name for r in runs if flags(r) and not failures(r)]
    broken = [r.name for r in runs if failures(r)]
    if broken:
        print(f"failed: {', '.join(broken)}")
    if flagged:
        print(f"flagged for review: {', '.join(flagged)}")

    if args.json:
        args.json.write_text(
            json.dumps(
                [
                    {
                        "name": r.name,
                        "brief": r.brief,
                        "elapsed_s": round(r.elapsed, 1),
                        "summary": r.summary,
                        "failures": failures(r),
                        "flags": flags(r),
                        "events": r.events,
                    }
                    for r in runs
                ],
                indent=1,
            )
        )
        print(f"raw events → {args.json}")

    sys.exit(1 if broken else 0)


if __name__ == "__main__":
    asyncio.run(main())
