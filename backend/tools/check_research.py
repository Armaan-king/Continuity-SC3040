"""Opt-in live Exa smoke check: at most one Search and one Contents request.

Run from backend with `python tools/check_research.py`. Reads the server environment;
never prints credentials or provider error bodies. No model calls are made.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from continuity.designs.context import ExecutionContext, current
from continuity.parts import research


async def main():
    token = current.set(ExecutionContext("manual-research-check"))
    try:
        search = await research.search_documents("Sensirion SHT40 manufacturer datasheet supply voltage")
        urls = [document["url"] for document in search["documents"]]
        contents = await research.read_documents(urls[:1]) if urls else {"documents": [], "error": "no_search_documents"}
        result = {
            "configured": research.configured(),
            "search": {"documents": len(urls), "error": search.get("error"), "cost_usd": search.get("cost_usd")},
            "contents": {"documents": len(contents["documents"]), "error": contents.get("error"),
                         "partial": contents.get("partial", False), "cost_usd": contents.get("cost_usd")},
            "sources": [{"url": document["url"], "characters": len(document["text"]),
                         "verification": document["verification"]} for document in contents["documents"]],
        }
        print(json.dumps(result, indent=2))
        return 0 if urls and contents["documents"] and all(d["text"] for d in contents["documents"]) else 1
    finally:
        current.reset(token)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
