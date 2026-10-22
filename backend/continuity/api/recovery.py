"""Recover interrupted durable inputs while the hosting process is awake."""
from __future__ import annotations

import asyncio
import logging
from psycopg.rows import dict_row
from fastapi import HTTPException

from . import bom, events
from .execution import _run, _run_bom
from .state import STREAMS
from .state import BOMS, FINDING_RECORDERS
from .memory import FindingRecorder

log = logging.getLogger(__name__)


async def recover(app) -> None:
    store = app.state.store
    while True:
        try:
            await store.recover_stale_runs()
            async with store.pool.connection() as conn:
                cursor = await conn.cursor(row_factory=dict_row).execute("""
                    SELECT t.id, t.user_id, t.last_seq, i.kind, i.payload
                    FROM threads t JOIN run_inputs i ON i.thread_id = t.id
                    WHERE t.status = 'abandoned' AND i.cancelled = false AND i.automatic_retries < 2
                    ORDER BY t.updated_at LIMIT 10""")
                rows = await cursor.fetchall()
            for row in rows:
                try:
                    app.state.runs.admit(row["user_id"])
                except HTTPException:
                    continue
                thread_id = row["id"]
                stream = STREAMS[thread_id] = events.EventStream(thread_id, last_seq=row["last_seq"])
                if row["kind"] == "bom":
                    producer = _run_bom([bom.BomRow(**item) for item in row["payload"]["rows"]], row["payload"].get("brief"), stream, store)
                else:
                    snapshot = await app.state.graph.aget_state({"configurable": {"thread_id": thread_id}})
                    if snapshot.values and not snapshot.next:
                        # Final checkpoint is already complete. Do not replay inference.
                        async with store.pool.connection() as conn:
                            await conn.execute("UPDATE threads SET status = 'done' WHERE id = %s AND status = 'abandoned' AND revision_id IS NOT NULL", (thread_id,))
                            await conn.execute("UPDATE run_inputs SET automatic_retries = 2 WHERE thread_id = %s", (thread_id,))
                        continue
                    producer = _run(app.state.graph, thread_id, None if snapshot.values else row["payload"], store, row["user_id"])
                try:
                    recorder = FindingRecorder()
                    for event in await store.run_events(thread_id):
                        recorder.feed(event)
                    FINDING_RECORDERS[thread_id] = recorder
                    def cleanup(key=thread_id):
                        for cache in (STREAMS, BOMS, FINDING_RECORDERS):
                            cache.pop(key, None)
                    await app.state.runs.start(thread_id, row["user_id"], producer, store, cleanup, last_seq=row["last_seq"])
                    async with store.pool.connection() as conn:
                        await conn.execute("UPDATE run_inputs SET automatic_retries = automatic_retries + 1 WHERE thread_id = %s", (thread_id,))
                except HTTPException:
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Durable run recovery could not complete")
        await asyncio.sleep(15)
