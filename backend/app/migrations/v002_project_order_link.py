"""Add order_id FK to projects table."""
from __future__ import annotations
from sqlalchemy import text

version = 2
name = "project_order_link"


async def up(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(projects)"))).fetchall()}
    if "order_id" not in cols:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN order_id INTEGER REFERENCES orders(id)"))


async def down(conn) -> None:
    # SQLite: recreate projects table without order_id
    await conn.execute(text("""
        CREATE TABLE projects_tmp (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            machine_uuid TEXT,
            process_uuid TEXT,
            notes TEXT,
            result_file_id INTEGER,
            source_app TEXT,
            source_user TEXT,
            source_layout_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """))
    await conn.execute(text("""
        INSERT INTO projects_tmp
        SELECT id, name, machine_uuid, process_uuid, notes, result_file_id,
               source_app, source_user, source_layout_id, created_at, updated_at
        FROM projects
    """))
    await conn.execute(text("DROP TABLE projects"))
    await conn.execute(text("ALTER TABLE projects_tmp RENAME TO projects"))
