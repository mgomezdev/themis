"""Add api_keys table for API key authentication."""
from __future__ import annotations
from sqlalchemy import text

version = 12
name = "api_keys"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) NOT NULL,
            key_prefix VARCHAR(16) NOT NULL,
            key_hash VARCHAR(64) NOT NULL,
            scopes TEXT NOT NULL DEFAULT '[]',
            enabled BOOLEAN NOT NULL DEFAULT 1,
            created_at VARCHAR(32) NOT NULL,
            last_used_at VARCHAR(32),
            revoked_at VARCHAR(32)
        )
    """))
    await conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_api_keys_prefix ON api_keys (key_prefix)"
    ))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS api_keys"))
