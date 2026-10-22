"""Prepare a reference board for README videos in a local disposable database.

Supplier values come from committed historical fixtures. Exa retrieval is live and
bounded to two requests. Credentials and full source text stay in ignored local data.
"""
import asyncio
import json
import os
import secrets
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from psycopg_pool import AsyncConnectionPool
from fuze import env
from fuze.api.auth import hasher
from fuze.api.revision_store import commit_revision
from fuze.api.store import Store
from fuze.designs.context import ExecutionContext, current
from fuze.designs.service import digest, document
from fuze.engine.models import Board, PartSpec, Rail, Requirements, Slot
from fuze.parts import research, search, specifications


def recorded_part(filename, mpn, role):
    data = json.loads((ROOT / "backend" / "fixtures" / filename).read_text(encoding="utf-8"))
    row = next(item for item in data["response"]["results"] if item["model"] == mpn)
    candidate = search._candidate(row)
    values, sources = specifications.parse(candidate.specs)
    evidence = {name: {"value": values[name], "parameter": source,
                      "original": candidate.specs[source], "source": candidate.product_url,
                      "method": "deterministic", "parser_version": "verified-2",
                      "source_hash": digest(dict(candidate.specs))}
                for name, source in sources.items()}
    part = PartSpec(mpn=candidate.mpn, manufacturer=candidate.manufacturer,
                    description=candidate.description, category=candidate.category,
                    package=candidate.package, stock=candidate.stock,
                    unit_price=candidate.unit_price, distributor="JLCPCB",
                    product_url=candidate.product_url, datasheet=candidate.product_url,
                    role=role, raw=dict(candidate.specs), provenance=sources,
                    fact_evidence=evidence, **values)
    return candidate, part


async def main():
    env.load()
    url = os.environ["DATABASE_URL"]
    parsed = urlsplit(url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or "media" not in parsed.path:
        raise RuntimeError("Use a local, disposable media database")
    # Explicitly bounded live retrieval; no model calls or live supplier requests.
    os.environ.update(FUZE_RESEARCH_ENABLED="1", FUZE_RESEARCH_MAX_CALLS="2",
                      FUZE_RESEARCH_BUDGET_USD="0.04", FUZE_FIXTURES="0")
    candidate, sensor = recorded_part("jlc_search.3e117ff670136d31.json", "SHT40-AD1B-R2", "peripheral")
    _, controller = recorded_part("jlc_search.8bfe442977505a39.json", "ESP32-S3-WROOM-1-N16R8", "master")
    work = ROOT / ".local" / "review-recording"
    if "--reuse-research" in sys.argv:
        # A visual retake should not spend another API request. This is an actual
        # previously exported response, retaining the provider's retrieval timestamp.
        exported = json.loads((work / "report.json").read_text(encoding="utf-8"))
        documents = tuple(exported["board"]["slots"]["sensor"]["part"]["research"])
        requests = 0
    else:
        token = current.set(ExecutionContext("readme-reference-research"))
        try:
            documents = await research.investigate(candidate, ["maximum supply current"])
            requests = current.get().research_calls
        finally:
            current.reset(token)
    if not documents:
        raise RuntimeError("Exa did not return documents; do not label this as a successful research demonstration")
    sensor = replace(sensor, research=documents)
    board = Board(Requirements(input_source="external", input_voltage=3.3),
        {"controller": Slot("controller", "Controller", "core", part=controller),
         "sensor": Slot("sensor", "Humidity Sensor", "peripherals", part=sensor)},
        {"3V3": Rail("3V3", 3.3, members=("controller", "sensor"),
                     basis="Reference design connection; confirm against the circuit")})
    saved = document(board, prompt="Reference design: 3.3 V temperature and humidity monitor",
                     confirmed=["input_voltage"], origin="brief")
    identity = uuid.uuid4().hex
    credentials = {"email": f"media-{identity[:12]}@example.com", "password": secrets.token_urlsafe(24)}
    async with AsyncConnectionPool(url, open=False) as pool:
        store = Store(pool)
        await store.setup()
        user = await store.create_user(credentials["email"], hasher.hash(credentials["password"]))
        await store.mark_onboarded(user.id)
        project = await store.create_project(user.id, "Sensor Review - Reference Board")
        thread = f"media-review-{identity}"
        await store.create_thread(thread, project.id, user.id, saved["prompt"])
        await store.save_progress(thread, 0, "done")
        async with pool.connection() as conn:
            async with conn.transaction():
                revision = await commit_revision(conn, thread, saved, "Reference design with recorded supplier data")
    work.mkdir(parents=True, exist_ok=True)
    metadata = {"preparedAt": datetime.now(timezone.utc).isoformat(),
                "projectId": project.id, "threadId": thread, "initialRevision": revision,
                "referenceData": "Historical distributor fixtures; manually assembled reference board; AI planning disabled",
                "research": {"provider": "exa", "requests": requests, "documents": len(documents),
                             "sources": [item["url"] for item in documents], "verification": "unverified",
                             "retrievedAt": [item["retrieved_at"] for item in documents],
                             "reusedForRetake": "--reuse-research" in sys.argv}}
    (work / "session.json").write_text(json.dumps({**metadata, "credentials": credentials}), encoding="utf-8")
    print(json.dumps({"prepared": True, "counts": saved["report"]["counts"], "research": metadata["research"]}, indent=2))


if __name__ == "__main__":
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main())
