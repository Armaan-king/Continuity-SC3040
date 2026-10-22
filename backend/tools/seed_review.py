"""Add reference designs to the disposable browser-test account."""
import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from psycopg_pool import AsyncConnectionPool
from continuity.api.store import Store
from continuity.api.auth import hasher
from continuity.api.revision_store import commit_revision
from continuity.designs.service import document
from continuity.engine.models import Board, Requirements, Rail, Slot, PartSpec


async def main():
    async with AsyncConnectionPool(os.environ["DATABASE_URL"], open=False) as pool:
        store = Store(pool)
        # Separate account keeps the analytics reference corpus unchanged.
        async with pool.connection() as conn:
            await conn.execute("DELETE FROM users WHERE email = %s", ("review-e2e@continuity.dev",))
        user = await store.create_user("review-e2e@continuity.dev", hasher.hash(os.environ["CONTINUITY_SEED_PASSWORD"]))
        project = await store.create_project(user.id, "Verified reference design")
        await store.create_thread("review-e2e-reference", project.id, user.id, "3.3 V reference sensor")
        await store.save_progress("review-e2e-reference", 0, "done")
        part = PartSpec("REFERENCE-SENSOR", "Reference fixture", "Synthetic browser-test component", "sensor",
                        vmin=1.8, vmax=3.6, i_peak=0.01, stock=1000, unit_price=2,
                        interfaces=("I2C",), role="peripheral")
        board = Board(Requirements(input_voltage=3.3), {"sensor": Slot("sensor", "Sensor", "peripherals", part=part)},
                      {"input": Rail("input", 3.3, members=("sensor",), i_limit=1, basis="Reference fixture")})
        async with pool.connection() as conn:
            async with conn.transaction():
                await commit_revision(conn, "review-e2e-reference", document(board), "Reference fixture")
        battery_project = await store.create_project(user.id, "Battery answers reference")
        await store.create_thread("review-e2e-battery", battery_project.id, user.id, "Compact battery-powered reference")
        await store.save_progress("review-e2e-battery", 0, "done")
        battery = replace(board, requirements=Requirements(input_source="battery-3v7", input_voltage=3.7),
                          rails={"input": replace(board.rails["input"], voltage=3.7)})
        async with pool.connection() as conn:
            async with conn.transaction():
                await commit_revision(conn, "review-e2e-battery", document(battery, prompt="Compact battery-powered reference"), "Reference fixture")
    print("Seeded versioned reference design for browser validation.")


if __name__ == "__main__":
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main())
