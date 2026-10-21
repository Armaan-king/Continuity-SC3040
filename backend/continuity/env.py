"""Load `.env` once, from wherever the process was started.

Secrets belong in a file that is gitignored, not in a shell history. `.env` is read at
the API entry point and by anything that needs a key directly; the engine never calls
this, because the engine never reads configuration at all.

**A real environment variable always wins.** Deployment sets variables directly, and a
stale `.env` left in a working tree must not override what the platform injected.
"""

from __future__ import annotations

import os
from pathlib import Path

_loaded = False

SEARCH_FROM = Path(__file__).resolve().parent.parent
"""`backend/`. Walks upward, so a `.env` at the repo root is found too."""


def load(*, override: bool = False) -> Path | None:
    """Read the nearest `.env`. Idempotent — later calls are free."""
    global _loaded
    if _loaded and not override:
        return None

    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:
        _loaded = True
        return None

    path = find_dotenv(usecwd=True) or find_dotenv(str(SEARCH_FROM / ".env"))
    if path:
        load_dotenv(path, override=override)
    _loaded = True
    return Path(path) if path else None


def flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


ROOT = SEARCH_FROM
"""`backend/`. What every bundled path is measured from."""


def under_root(relative: str, variable: str) -> Path:
    """A bundled directory, anchored to the package unless the deployment names another.

    `Path("cache/normalized")` resolves against the *working directory*. On a laptop that
    is `backend/`, because that is where the server is started from; in a container image
    it is usually `/app` or `/`, and then the committed parse cache is simply not there.
    Nothing fails loudly — the cache misses, every part is fetched and parsed again, and a
    fresh empty directory appears wherever the process happened to start. On a read-only
    filesystem the writes fail too, quietly, because a cache that cannot be written is
    not an error worth stopping a run for.

    The environment variable comes first so a deployment can point these at a mounted
    volume. Where there is no volume — a free tier with an ephemeral filesystem — the
    committed cache is still found, and what the run adds to it is lost on restart, which
    is the correct behaviour for a cache.
    """
    stated = os.environ.get(variable)
    return Path(stated) if stated else ROOT / relative


def cache_dir(name: str) -> Path:
    """`backend/cache/<name>`, or under `CONTINUITY_CACHE_DIR` when one is set."""
    return under_root("cache", "CONTINUITY_CACHE_DIR") / name
