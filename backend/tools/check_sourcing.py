"""Opt-in live sourcing check: at most one Exa and one model request.

Run from backend: python tools/check_sourcing.py
Uses configured credentials without printing them. No database writes, user projects,
or automatic retries. JLCPCB lookup uses the normal live supplier adapter.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from continuity import env, llm
from continuity.designs.context import ExecutionContext, current
from continuity.graph import sourcing
from continuity.parts import evidence, research


async def main():
    env.load()
    # These caps affect this process only, never Render or the saved .env file.
    os.environ.update(CONTINUITY_RESEARCH_MAX_CALLS="1", CONTINUITY_RESEARCH_BUDGET_USD="0.02",
                      CONTINUITY_LLM_MAX_CALLS="1", CONTINUITY_EVIDENCE_MAX_PARTS="1")
    if not sourcing.web_enabled() or not llm.available():
        print(json.dumps({"error": "Live Exa and an AI provider must be configured; no request sent."}))
        return 1
    context = ExecutionContext("manual-multi-source-check")
    token = current.set(context)
    try:
        # No fitness-classification call: spend the one model allowance on source
        # extraction, then let the same fact-admission code used by the app check it.
        found = await sourcing.find("low power microcontroller hardware I2C bus", constraint={"category": "mcu"})
        web = [candidate for candidate in found if candidate.web and not candidate.lcsc]
        eligible = [c for c in web if evidence.manufacturer_source(c, c.product_url)]
        part = await sourcing.choose(eligible[0]) if eligible else None
        checked = len(part.fact_evidence) if part else 0
        output = {
            "scope": "Provider wiring and candidate admission; not hardware compatibility",
            "shortlist": [{"mpn": c.mpn, "manufacturer": c.manufacturer, "source": c.source} for c in found],
            "external_product": None if part is None else {
                "mpn": part.mpn, "source": part.product_url, "stock": part.stock, "price": part.unit_price,
                "source_checked_fields": checked, "interfaces": part.interfaces,
                "documents": len(part.research),
                "extraction": [doc.get("extraction_status") for doc in part.research]},
            "research": research.status()["activity"], "ai": llm.public_status()["activity"],
            "database_writes": 0,
        }
        print(json.dumps(output, indent=2))
        # A successful extraction may admit zero facts. That is a valid UNKNOWN
        # outcome, not a reason to keep spending requests until something passes.
        return 0 if (part and context.research_calls == 1 and context.llm_calls == 1
                     and output["ai"]["requests"]["succeeded"] == 1) else 1
    finally:
        current.reset(token)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
