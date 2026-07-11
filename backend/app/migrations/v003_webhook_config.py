"""Add webhook_config table."""
from __future__ import annotations
from sqlalchemy import text

version = 3
name = "webhook_config"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS webhook_config (
            id INTEGER PRIMARY KEY,
            url TEXT,
            secret TEXT,
            events TEXT NOT NULL DEFAULT '[]'
        )
    """))
    await conn.execute(text(
        "INSERT OR IGNORE INTO webhook_config (id, events) VALUES (1, '[]')"
    ))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS webhook_config"))
