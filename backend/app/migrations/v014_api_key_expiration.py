"""Add nullable expires_at column to api_keys table for key expiration support."""
from __future__ import annotations
from sqlalchemy import text

version = 14
name = "api_key_expiration"


async def up(conn) -> None:
    info = (await conn.execute(text("PRAGMA table_info(api_keys)"))).fetchall()
    cols = {row[1] for row in info}
    if "expires_at" in cols:
        return  # already added (e.g. fresh schema created from current models.py)
    await conn.execute(text("""
        ALTER TABLE api_keys
        ADD COLUMN expires_at VARCHAR(32)
    """))


async def down(conn) -> None:
    await conn.execute(text("""
        ALTER TABLE api_keys
        DROP COLUMN expires_at
    """))
