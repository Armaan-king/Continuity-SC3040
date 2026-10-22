"""Execution-local durable stage journal, shared by graph and BOM workers."""
from __future__ import annotations

from contextvars import ContextVar
import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from .service import digest


@dataclass
class ExecutionContext:
    thread_id: str
    store: Any = None
    research_calls: int = 0
    evidence_parts: int = 0
    evidence_cache: dict = field(default_factory=dict)
    evidence_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    research_cache: dict = field(default_factory=dict)
    researched_parts: dict = field(default_factory=dict)
    research_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    discovery_queries: set[str] = field(default_factory=set)
    llm_cache: dict = field(default_factory=dict)
    llm_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    llm_calls: int = 0
    llm_tokens: int = 0
    llm_unavailable: str | None = None


current: ContextVar[ExecutionContext | None] = ContextVar("design_execution", default=None)


async def stage(name: str, inputs: Any, work: Callable[[], Awaitable[Any]]) -> Any:
    context = current.get()
    key = f"{name}:{digest(inputs)}"
    if context and context.store:
        saved = await context.store.stage_result(context.thread_id, key)
        if saved is not None:
            return saved["value"]
    value = await work()
    if context and context.store and value is not None:
        await context.store.save_stage(context.thread_id, key, {"value": value})
    return value
