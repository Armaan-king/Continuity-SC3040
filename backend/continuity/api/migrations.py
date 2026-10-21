"""Transactional, versioned migrations with checksums and a PostgreSQL lock."""

from __future__ import annotations

import hashlib
from pathlib import Path

MIGRATIONS = Path(__file__).with_name("migrations")
BASELINE = Path(__file__).with_name("schema.sql")


async def migrate(pool) -> None:
    scripts = [("001_baseline", BASELINE)] + [(p.stem, p) for p in sorted(MIGRATIONS.glob("*.sql"))]
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(3040001)")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version text PRIMARY KEY,
                    checksum text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
            """)
            for version, path in scripts:
                sql = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                cursor = await conn.execute(
                    "SELECT checksum FROM schema_migrations WHERE version = %s", (version,)
                )
                existing = await cursor.fetchone()
                if existing:
                    if existing[0] != checksum:
                        raise RuntimeError(f"Migration {version} was modified after application")
                    continue
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                    (version, checksum),
                )


async def main() -> None:
    from psycopg_pool import AsyncConnectionPool
    from ..settings import Settings

    settings = Settings.read()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required for migrations")
    async with AsyncConnectionPool(settings.database_url, open=False,
                                    kwargs={"autocommit": True}) as pool:
        await migrate(pool)


if __name__ == "__main__":
    import asyncio
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main())
