"""Client for pcbparts-mcp — JSON-RPC over streamable HTTP.

`https://pcbparts.dev/mcp`, no auth, 100 requests/minute per IP. Verified against
v0.5.2 rather than assumed.

## Why this is hand-rolled rather than an MCP SDK

The server is *stateless* streamable HTTP: it answers `tools/call` without an
`initialize` handshake or a session id. A full client would negotiate a session we
never use and add a dependency to send one POST and parse the reply. Replies are
SSE-framed (`event: message` / `data: {...}`) even for a single response, which is the
only part that needs care.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any

import httpx

from . import fixtures

ENDPOINT = "https://pcbparts.dev/mcp"
HEALTH = "https://pcbparts.dev/health"

TIMEOUT_S = 30.0
MAX_ATTEMPTS = 3
BACKOFF_S = 1.5

RATE_LIMIT_PER_MIN = 100
"""Per IP. We stay well under it, but a repair loop re-searching is the risk case."""


class ToolError(RuntimeError):
    """The server answered, and the answer was an error."""


class _Limiter:
    """Token bucket over a rolling minute.

    Fixed spacing was costing more than the quota did. Placing one part takes a search,
    an enrichment lookup and sometimes a pinout — so a four-slot board with three
    repairs is around 28 calls, and 0.6 s between each is 17 seconds of pure waiting for
    a quota that would have allowed the whole run in one burst.

    A bucket only blocks once the *minute* is genuinely spent, which is what the server
    actually limits.
    """

    def __init__(self, per_minute: int, window: float = 60.0) -> None:
        self._capacity = per_minute
        self._window = window
        self._times: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            while self._times and now - self._times[0] >= self._window:
                self._times.popleft()

            if len(self._times) >= self._capacity:
                await asyncio.sleep(self._window - (now - self._times[0]) + 0.01)
                now = loop.time()
                while self._times and now - self._times[0] >= self._window:
                    self._times.popleft()

            self._times.append(loop.time())


_limiter = _Limiter(RATE_LIMIT_PER_MIN)


def _parse_sse(body: str) -> dict[str, Any]:
    """Pull the JSON-RPC envelope out of an SSE body.

    Single responses still arrive framed, so the payload is on a `data:` line rather
    than being the body itself.
    """
    for line in body.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return json.loads(body)  # some deployments answer with plain JSON


def _unwrap(envelope: dict[str, Any], tool: str) -> Any:
    if "error" in envelope:
        raise ToolError(f"{tool}: {envelope['error'].get('message', envelope['error'])}")

    result = envelope.get("result", {})
    if result.get("isError"):
        raise ToolError(f"{tool}: {result}")

    content = result.get("content") or []
    if not content:
        return result

    text = content[0].get("text", "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text  # a tool that answers in prose; the caller decides what to do


async def call_tool(tool: str, arguments: dict[str, Any]) -> Any:
    """Call one MCP tool. Replays from fixtures when asked; records when not."""
    arguments = {k: v for k, v in arguments.items() if v is not None}

    if fixtures.replaying():
        return fixtures.require(tool, arguments)

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        await _limiter.wait()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as http:
                response = await http.post(ENDPOINT, json=payload, headers=headers)
                response.raise_for_status()
                result = _unwrap(_parse_sse(response.text), tool)
        except ToolError:
            raise  # the server understood us and said no; retrying will not help
        except Exception as error:  # network, timeout, malformed body
            last_error = error
            if attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(BACKOFF_S * (attempt + 1))
            continue

        fixtures.save(tool, arguments, result)
        return result

    raise ToolError(f"{tool}: unreachable after {MAX_ATTEMPTS} attempts ({last_error})")


async def healthy() -> bool:
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            return (await http.get(HEALTH)).json().get("status") == "healthy"
    except Exception:
        return False
