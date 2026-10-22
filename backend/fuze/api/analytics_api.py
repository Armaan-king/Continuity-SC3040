"""`GET /analytics`: the read path behind DS-16, and the only queries it runs.

Every query is scoped to the signed-in engineer with a `WHERE` on `user_id`, joins the
owning project on both id and user so a mismatched row cannot leak through, and skips
the walkthrough project. They run in one read-only transaction: the analysis can be
viewed any number of times and the database itself refuses to let it write.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.rows import dict_row

from .analytics import BomItem, CheckRow, FindingRow, Records, SelectionRow, build_analytics
from .auth import current_user, store_of
from .store import Store, User

log = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])

_OWNED = """
      JOIN projects p ON p.id = t.project_id AND p.user_id = t.user_id
     WHERE t.user_id = %(user)s AND NOT p.is_walkthrough
"""
"""The ownership boundary every query below shares."""

_COMPLETED = _OWNED + "       AND t.status = 'done'\n"

RUNS = "SELECT t.status, COUNT(*)::integer AS n FROM threads t" + _OWNED + " GROUP BY t.status"

CHECKS = (
    """
    SELECT e.thread_id, e.seq,
           COALESCE(e.event->>'slot', '') AS slot,
           e.event->>'rule' AS rule,
           e.event->>'status' AS status,
           COALESCE((e.event->>'unchecked')::boolean, false) AS unchecked
      FROM run_events e
      JOIN threads t ON t.id = e.thread_id
    """
    + _COMPLETED
    + "       AND e.event->>'type' = 'check' AND e.event->>'rule' IS NOT NULL"
)
"""Frames recorded before `unchecked` existed have no key, and read as a genuine warn."""

SELECTIONS = (
    """
    SELECT e.thread_id, e.seq,
           COALESCE(e.event->>'slot', '') AS slot,
           e.event->'part'->>'mpn' AS mpn,
           e.event->'part'->>'lifecycle' AS lifecycle
      FROM run_events e
      JOIN threads t ON t.id = e.thread_id
    """
    + _COMPLETED
    + "       AND e.event->>'type' = 'selection' AND NULLIF(e.event->'part'->>'mpn', '') IS NOT NULL"
)

FINDINGS = (
    """
    SELECT f.thread_id, f.project_id, f.rule, f.slot, f.outcome, f.action, f.signature, f.worked
      FROM findings f
      JOIN threads t ON t.id = f.thread_id AND t.user_id = f.user_id
    """
    + _COMPLETED
)

BOM = (
    """
    SELECT t.id AS thread_id,
           item->>'mpn' AS mpn,
           item->>'distributor' AS distributor,
           item->>'lifecycle' AS lifecycle
      FROM threads t
      CROSS JOIN LATERAL jsonb_array_elements(COALESCE(t.bom, '[]'::jsonb)) AS item
    """
    + _COMPLETED
    + "       AND NULLIF(item->>'mpn', '') IS NOT NULL"
)


async def load_records(store: Store, user_id: str) -> Records:
    params = {"user": user_id}
    async with store.pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("SET TRANSACTION READ ONLY")
            runs = await _fetch(conn, RUNS, params)
            checks = await _fetch(conn, CHECKS, params)
            selections = await _fetch(conn, SELECTIONS, params)
            findings = await _fetch(conn, FINDINGS, params)
            bom = await _fetch(conn, BOM, params)
    return Records(
        runs={row["status"]: row["n"] for row in runs},
        checks=[CheckRow(**row) for row in checks],
        findings=[FindingRow(**row) for row in findings],
        selections=[SelectionRow(**row) for row in selections],
        bom=[BomItem(**row) for row in bom],
    )


async def _fetch(conn: psycopg.AsyncConnection, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    cursor = await conn.cursor(row_factory=dict_row).execute(query, params)
    return await cursor.fetchall()


@router.get("/analytics")
async def analytics(request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    store = store_of(request)
    try:
        records = await load_records(store, user.id)
    except psycopg.Error as error:
        # UC-16.0.E.1: say the analysis could not be loaded; never return part of it.
        log.exception("analytics query failed for user %s", user.id)
        raise HTTPException(503, "the analysis could not be loaded — try again shortly") from error
    return build_analytics(records)
