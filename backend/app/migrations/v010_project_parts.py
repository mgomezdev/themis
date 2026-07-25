"""Add project_parts table for non-3D-printed parts (e.g. magnets, screws) needed by a project's assembly."""
from __future__ import annotations
from sqlalchemy import text

version = 10
name = "project_parts"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS project_parts (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL
                REFERENCES projects(id) ON DELETE CASCADE,
            name       TEXT    NOT NULL,
            quantity   INTEGER NOT NULL DEFAULT 1,
            allocated  BOOLEAN NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT    NOT NULL DEFAULT ''
        )
    """))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS project_parts"))
