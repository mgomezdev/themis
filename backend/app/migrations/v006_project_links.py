"""Add project_links table for user-defined URLs attached to a project."""
from __future__ import annotations
from sqlalchemy import text

version = 6
name = "project_links"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS project_links (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL
                REFERENCES projects(id) ON DELETE CASCADE,
            url        TEXT    NOT NULL,
            label      TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT    NOT NULL DEFAULT ''
        )
    """))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS project_links"))
