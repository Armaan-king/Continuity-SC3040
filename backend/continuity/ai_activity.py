"""Safe provider receipts: no prompts, credentials, response bodies or user IDs."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import logging
import time
from typing import Callable, Iterator
import uuid

log = logging.getLogger(__name__)
_observer: ContextVar[Callable[[dict], None] | None] = ContextVar("ai_observer", default=None)
_identity: str | None = None
_requests: dict[str, int] = {}
_last_request: dict = {}


def _select(config) -> str:
    global _identity, _requests, _last_request
    # Changing credentials/model must never inherit a previous configuration's success.
    identity = hashlib.sha256(repr((config.provider, config.base_url, config.model, config.key)).encode()).hexdigest()
    if identity != _identity:
        _identity = identity
        _requests = dict(attempted=0, succeeded=0, failed=0, cancelled=0)
        _last_request = {"status": "not_tested", "at": None}
    return identity


def status(config) -> dict:
    _select(config)
    return {"scope": "current_process", "requests": dict(_requests), "last_request": dict(_last_request)}


@contextmanager
def observe(callback: Callable[[dict], None]) -> Iterator[None]:
    token = _observer.set(callback)
    try:
        yield
    finally:
        _observer.reset(token)


def notify(receipt: dict) -> None:
    callback = _observer.get()
    if callback:
        try:
            callback(dict(receipt))
        except Exception:
            # Diagnostics must not turn a successful paid response into a retry.
            log.warning("AI receipt could not be delivered to the progress stream")


def started(config, thread_id: str | None) -> dict:
    global _last_request
    identity = _select(config)
    receipt = {"request_id": uuid.uuid4().hex[:12], "provider": config.provider, "model": config.model}
    _requests["attempted"] += 1
    _last_request = {**receipt, "status": "started", "at": datetime.now(timezone.utc).isoformat()}
    log.info("ai_request started request_id=%s thread_id=%s provider=%s model=%s",
             receipt["request_id"], thread_id or "none", config.provider, config.model)
    return {**receipt, "_identity": identity, "_started": time.monotonic(), "_thread_id": thread_id}


def finished(attempt: dict, outcome: str, *, http_status: int | None = None,
             tokens: int | None = None, reason: str | None = None) -> None:
    global _last_request
    receipt = {key: value for key, value in attempt.items() if not key.startswith("_")}
    receipt.update(status=outcome, at=datetime.now(timezone.utc).isoformat(),
                   elapsed_ms=round((time.monotonic() - attempt["_started"]) * 1000),
                   http_status=http_status, tokens=tokens)
    if reason:
        receipt["reason"] = reason  # Only locally constructed, sanitized reasons.
    if attempt["_identity"] == _identity:
        _requests[{"success": "succeeded", "error": "failed", "cancelled": "cancelled"}[outcome]] += 1
        _last_request = receipt
    log.log(logging.WARNING if outcome == "error" else logging.INFO,
            "ai_request %s request_id=%s thread_id=%s provider=%s model=%s http_status=%s tokens=%s elapsed_ms=%s reason=%s",
            outcome, receipt["request_id"], attempt["_thread_id"] or "none", receipt["provider"],
            receipt["model"], http_status, tokens, receipt["elapsed_ms"], reason or "none")
    notify(receipt)


def message(receipt: dict) -> tuple[str, str | None]:
    """A receipt is a provider response, never a claim that the board is correct."""
    provider = {"groq": "Groq", "gemini": "Gemini", "zai": "Z.ai", "ollama": "Ollama"}.get(
        receipt["provider"], receipt["provider"])
    if receipt["status"] == "cached":
        return f"Reusing an AI response from this execution ({provider}); no new API request.", None
    if receipt["status"] == "success":
        usage = f", {receipt['tokens']} tokens" if receipt.get("tokens") is not None else ""
        return f"{provider} returned a JSON response ({receipt['model']}{usage}).", None
    if receipt["status"] == "cancelled":
        return f"{provider} request cancelled before a usable response was received.", "warning"
    return f"{provider} request failed: {receipt.get('reason', 'no usable response')}.", "warning"
