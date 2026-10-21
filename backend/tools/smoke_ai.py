"""Exercise a configured real model without distributor calls or fixture responses.

Run with the backend interpreter; output contains configuration metadata and results,
never credentials. Exits nonzero if either JSON completion or board planning fails.
"""

import asyncio
import json
import time

from continuity import llm
from continuity.planner.plan import plan_board


async def main():
    started = time.monotonic()
    print(json.dumps({"test": "real-ai", **llm.public_status()}), flush=True)
    response = await llm.complete_json(
        "Extract only explicit measurements in volts, without guessing missing values.",
        'Supply voltage: 1.8V to 3.6V. Return {"vmin":number,"vmax":number}.',
    )
    assert response.get("vmin") == 1.8 and response.get("vmax") == 3.6, response
    print(json.dumps({"case": "measurement-extraction", "result": "PASS", "response": response}), flush=True)
    plan = await plan_board("USB powered temperature and humidity monitor with an OLED display")
    assert plan is not None, "Provider did not produce a usable plan"
    assert len(plan.slots) >= 3 and plan.rails, "Missing functional blocks or power rails"
    print(json.dumps({"case": "board-planning", "result": "PASS", "slots": list(plan.slots),
                      "input_source": plan.requirements.input_source,
                      "elapsed_seconds": round(time.monotonic() - started, 2)}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
