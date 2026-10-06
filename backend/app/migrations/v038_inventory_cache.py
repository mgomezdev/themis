"""`inventory_cache` (BIZ-219): the last-known spools/materials of a REMOTE inventory provider, persisted so the UI keeps
showing (stale) data during an outage and across a restart."""
from __future__ import annotations
from sqlalchemy import text

version = 38
name = "inventory_cache"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS inventory_cache (
            provider VARCHAR(64) NOT NULL,
            kind VARCHAR(16) NOT NULL,
            payload JSON NOT NULL,
            fetched_at VARCHAR(32) NOT NULL,
            PRIMARY KEY (provider, kind)
        )
    """))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS inventory_cache"))
