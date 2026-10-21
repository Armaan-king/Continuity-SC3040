"""Record and replay distributor responses.

`CONTINUITY_FIXTURES=1` replays from `fixtures/` and never touches the network. Any
call without a recording is an error rather than a silent live fetch — a fixture run
that quietly reaches the internet is worse than no fixture mode at all, because it
looks offline right up until the wifi fails.

Otherwise calls go live and are recorded as they go, so a rehearsal produces the
fixtures the demo would replay.

This is the demo safety net, and it is disclosed in the pitch rather than hidden.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .. import env

FIXTURE_DIR = env.under_root("fixtures", "CONTINUITY_FIXTURE_DIR")


def replaying() -> bool:
    return os.environ.get("CONTINUITY_FIXTURES", "") == "1"


class MissingFixture(RuntimeError):
    """Replay was asked for something never recorded."""

    def __init__(self, key: str, label: str) -> None:
        super().__init__(
            f"no fixture for {label} ({key}). Record one by running without "
            f"CONTINUITY_FIXTURES=1, or check the arguments match a recorded call."
        )


def key_for(tool: str, arguments: dict[str, Any]) -> str:
    """Stable hash of a call. Sorted keys, so argument order cannot change the key."""
    payload = json.dumps({"tool": tool, "arguments": arguments}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _path(tool: str, key: str) -> Path:
    return FIXTURE_DIR / f"{tool}.{key}.json"


def load(tool: str, arguments: dict[str, Any]) -> Any | None:
    path = _path(tool, key_for(tool, arguments))
    if not path.exists():
        return None
    return json.loads(path.read_text())["response"]


def save(tool: str, arguments: dict[str, Any], response: Any) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    key = key_for(tool, arguments)
    _path(tool, key).write_text(
        json.dumps(
            {"tool": tool, "arguments": arguments, "response": response},
            indent=1,
            ensure_ascii=False,
        )
    )


def require(tool: str, arguments: dict[str, Any]) -> Any:
    recorded = load(tool, arguments)
    if recorded is None:
        raise MissingFixture(key_for(tool, arguments), tool)
    return recorded
