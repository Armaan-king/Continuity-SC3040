"""Validated runtime configuration. Public responses never expose secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import env


def integer(name: str, default: int, *, minimum: int = 1, maximum: int = 10000) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str | None
    origins: tuple[str, ...]
    cookie_secure: bool
    cookie_samesite: str
    max_active_runs: int
    run_timeout_seconds: int

    @classmethod
    def read(cls) -> Settings:
        env.load()
        environment = os.environ.get("CONTINUITY_ENV", "development").strip().lower()
        if environment not in {"development", "production", "test"}:
            raise ValueError("CONTINUITY_ENV must be development, production, or test")
        production = environment == "production"
        database_url = os.environ.get("DATABASE_URL") or None
        secure = env.flag("CONTINUITY_COOKIE_SECURE", production)
        samesite = os.environ.get("CONTINUITY_COOKIE_SAMESITE", "lax").lower()
        if samesite not in {"lax", "strict", "none"}:
            raise ValueError("CONTINUITY_COOKIE_SAMESITE must be lax, strict, or none")
        if samesite == "none" and not secure:
            raise ValueError("SameSite=None requires CONTINUITY_COOKIE_SECURE=1")
        if production and (not database_url or not secure):
            raise ValueError("Production requires DATABASE_URL and secure session cookies")
        origins = tuple(origin.strip().rstrip("/") for origin in os.environ.get(
            "CONTINUITY_ORIGINS", "" if production else "http://localhost:5173,http://localhost:5174"
        ).split(",") if origin.strip())
        if "*" in origins:
            raise ValueError("CONTINUITY_ORIGINS must list explicit origins")
        return cls(environment, database_url, origins, secure, samesite,
                   integer("CONTINUITY_MAX_ACTIVE_RUNS", 4, maximum=32),
                   integer("CONTINUITY_RUN_TIMEOUT_SECONDS", 600, maximum=3600))
