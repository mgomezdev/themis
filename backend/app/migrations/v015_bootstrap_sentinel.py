"""Add bootstrap_sentinel table for atomic bootstrap-key minting."""
from __future__ import annotations
from sqlalchemy import text

version = 15
name = "bootstrap_sentinel"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS bootstrap_sentinel (
            id INTEGER PRIMARY KEY,
            created_at VARCHAR(32) NOT NULL
        )
    """))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS bootstrap_sentinel"))
