"""Opt-in provider check: one planning request, no sourcing or database writes."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fuze import llm
from fuze.designs.context import current, ExecutionContext
from fuze.planner import plan as planner

BRIEF = """Design a compact, battery-powered device that measures the light reaching a houseplant. Use an ambient-light sensor connected to a low-power microcontroller over I²C, a small OLED showing the current light level, one button to switch display modes, and an indicator LED.

Power it from a single rechargeable 3.7 V Li-ion battery with USB-C charging. Include suitable charging, protection, and voltage regulation. No Wi-Fi or Bluetooth is needed. Prioritize compact size and low power consumption.

Ask me to confirm the required battery runtime, actual battery capacity, operating temperature range, current margin, and maximum component dimensions. Keep missing specifications unresolved."""


async def main() -> int:
    config = llm.configuration()
    if not config.enabled:
        print(json.dumps({"passed": False, "reason": "AI is not configured"}))
        return 1
    os.environ["FUZE_LLM_MAX_CALLS"] = "1"
    os.environ["FUZE_LLM_TOKEN_BUDGET"] = "10000"
    os.environ["FUZE_LLM_MAX_TOKENS"] = str(min(config.max_tokens, 4096))
    os.environ["FUZE_RESEARCH_ENABLED"] = "0"
    llm.MAX_ATTEMPTS = 1
    context = ExecutionContext("manual-planner-check")
    token = current.set(context)
    try:
        plan = await planner.plan_board(BRIEF)
        slots = [{"id": slot.id, "label": slot.label, "query": plan.queries[slot.id]}
                 for slot in plan.slots.values()] if plan else []
        has_light = any("light" in (slot["label"] + " " + slot["query"]).lower() for slot in slots)
        passed = bool(plan and has_light and not planner.structural_issues(plan, BRIEF))
        print(json.dumps({"passed": passed, "provider": config.provider, "model": config.model,
                          "http_attempts": context.llm_calls,
                          "accounted_tokens": context.llm_tokens,
                          "failure": planner.last_failure.get(), "slots": slots}, indent=2))
        return 0 if passed else 1
    finally:
        current.reset(token)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
