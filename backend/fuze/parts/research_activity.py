"""Exa request receipts, excluding queries, URLs, documents, headers and keys."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
import os
import time
import uuid

from .. import ai_activity, env
from ..designs.context import current

log = logging.getLogger(__name__)
_identity = None
_counts: dict = {}
_last_request: dict = {}
_last_event: dict = {}


def _select():
    global _identity, _counts, _last_request, _last_event
    identity = hashlib.sha256(repr((os.environ.get("EXA_API_KEY"),
        env.flag("FUZE_RESEARCH_ENABLED", True), env.flag("FUZE_FIXTURES"))).encode()).hexdigest()
    if identity != _identity:
        _identity = identity
        _counts = dict(attempted=0, succeeded=0, failed=0, cancelled=0, cache_hits=0, skipped=0)
        _last_request = {"status": "not_tested", "at": None}
        _last_event = dict(_last_request)
    return identity


def status() -> dict:
    _select()
    return {"scope": "current_process", "requests": {key: _counts[key] for key in (
        "attempted", "succeeded", "failed", "cancelled")}, "cache_hits": _counts["cache_hits"],
        "skipped": _counts["skipped"], "last_request": dict(_last_request), "last_event": dict(_last_event)}


def _receipt(operation, outcome, **fields):
    return {"provider": "exa", "operation": operation, "status": outcome,
            "at": datetime.now(timezone.utc).isoformat(), **fields}


def _publish(receipt, thread_id, *, update=True):
    global _last_event
    if update:
        _last_event = receipt
    log.log(logging.WARNING if receipt["status"] == "error" else logging.INFO,
            "exa_request %s request_id=%s thread_id=%s operation=%s http_status=%s documents=%s partial=%s cost_usd=%s reason=%s source=%s",
            receipt["status"], receipt.get("request_id", "none"), thread_id or "none", receipt["operation"],
            receipt.get("http_status"), receipt.get("documents"), receipt.get("partial"),
            receipt.get("cost_usd"), receipt.get("reason", "none"), receipt.get("source", "none"))
    ai_activity.notify(receipt)


def started(operation):
    global _last_request
    identity = _select()
    context = current.get()
    thread_id = context.thread_id if context else None
    receipt = _receipt(operation, "started", request_id=uuid.uuid4().hex[:12])
    _counts["attempted"] += 1
    _last_request = receipt
    # Starts are logged; only completed or skipped work is added to the timeline.
    log.info("exa_request started request_id=%s thread_id=%s operation=%s",
             receipt["request_id"], thread_id or "none", operation)
    return {**receipt, "_identity": identity, "_thread_id": thread_id, "_started": time.monotonic()}


def finished(attempt, outcome, *, http_status=None, documents=0, partial=False, cost_usd=None, reason=None):
    global _last_request
    receipt = _receipt(attempt["operation"], outcome, request_id=attempt["request_id"],
                       elapsed_ms=round((time.monotonic() - attempt["_started"]) * 1000),
                       http_status=http_status, documents=documents, partial=partial, cost_usd=cost_usd)
    if reason:
        receipt["reason"] = reason
    selected = attempt["_identity"] == _select()
    if selected:
        _counts[{"success": "succeeded", "error": "failed", "cancelled": "cancelled"}[outcome]] += 1
        _last_request = receipt
    _publish(receipt, attempt["_thread_id"], update=selected)


def no_request(operation, outcome, *, reason=None, source=None):
    _select()
    _counts["cache_hits" if outcome == "cached" else "skipped"] += 1
    context = current.get()
    receipt = _receipt(operation, outcome)
    if reason:
        receipt["reason"] = reason
    if source:
        receipt["source"] = source
    _publish(receipt, context.thread_id if context else None)


def discovered(count):
    _select()
    context = current.get()
    receipt = _receipt("discovery", "result", candidates=count)
    log.info("exa_discovery candidates=%s thread_id=%s", count, context.thread_id if context else "none")
    _publish(receipt, context.thread_id if context else None)


def message(receipt):
    operation = {"search": "search", "contents": "document retrieval", "evidence": "evidence research",
                 "discovery": "alternative discovery"}.get(receipt["operation"], "research")
    outcome = receipt["status"]
    if outcome == "result":
        count = receipt["candidates"]
        return (f"Exa discovery identified {count} candidate product(s) with matching source passages. "
                "Electrical compatibility still needs validation.", None if count else "warning")
    if outcome == "cached":
        problem = " The cached result records a retrieval failure." if receipt.get("reason") == "cached_failure" else ""
        return f"Reusing saved Exa {operation}; no new API request.{problem}", "warning" if problem else None
    if outcome == "success":
        count = receipt["documents"]
        text = f"Exa {operation} completed: {count} usable document(s)."
        if receipt.get("partial"):
            text += " Some requested URLs could not be retrieved."
        if receipt.get("cost_usd") is not None:
            text += f" Exa-reported request cost: ${receipt['cost_usd']:.4f}."
        return text, "warning" if receipt.get("partial") else None
    if outcome == "cancelled":
        return f"Exa {operation} cancelled before a usable response was received.", "warning"
    if outcome == "error":
        code = receipt.get("http_status")
        reason = f"HTTP {code}" if code and code != 200 else "no usable response"
        return f"Exa {operation} failed ({reason}); missing evidence remains unresolved.", "warning"
    reasons = {
        "disabled": "research is disabled or its key is missing",
        "fixtures": "recorded supplier mode is active",
        "no_run_context": "there is no active research budget",
        "budget_exhausted": "the run's research allowance is exhausted",
        "budget_or_duplicate_request": "the request was already reserved or the research allowance is exhausted",
        "no_public_urls": "no supported public document URL was supplied",
        "evidence_complete": "no additional supported evidence fields were requested",
        "ai_unavailable": "AI evidence processing is unavailable",
        "evidence_budget": "the run's evidence-processing allowance is exhausted",
        "discovery_budget": "the alternative-discovery allowance is exhausted",
    }
    level = None if receipt.get("reason") in {"evidence_complete", "fixtures"} else "warning"
    return f"Exa {operation} skipped: {reasons.get(receipt.get('reason'), 'no new retrieval is needed')}. No API request sent.", level
