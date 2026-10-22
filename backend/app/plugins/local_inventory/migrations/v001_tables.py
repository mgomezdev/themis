"""Local inventory's own tables (prefix `local_inv_`): materials, spools and an audit log of weight changes. They may
reference each other; nothing in core references them (spec §3.8)."""
from __future__ import annotations

from sqlalchemy import text

version = 1
name = "tables"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS local_inv_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(200) NOT NULL,
            material VARCHAR(64),
            vendor VARCHAR(200),
            color_hex VARCHAR(16),
            density FLOAT,
            diameter FLOAT,
            profile_links TEXT NOT NULL DEFAULT '{}',
            archived BOOLEAN NOT NULL DEFAULT 0,
            created_at VARCHAR(32) NOT NULL,
            updated_at VARCHAR(32) NOT NULL
        )
    """))
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS local_inv_spools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL REFERENCES local_inv_materials(id) ON DELETE RESTRICT,
            label VARCHAR(200) NOT NULL,
            location VARCHAR(200),
            initial_g FLOAT,
            remaining_g FLOAT,
            archived BOOLEAN NOT NULL DEFAULT 0,
            created_at VARCHAR(32) NOT NULL,
            updated_at VARCHAR(32) NOT NULL
        )
    """))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_local_inv_spools_material ON local_inv_spools (material_id)"))
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS local_inv_weight_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spool_id INTEGER NOT NULL REFERENCES local_inv_spools(id) ON DELETE CASCADE,
            old_g FLOAT,
            new_g FLOAT,
            source VARCHAR(32) NOT NULL,
            at VARCHAR(32) NOT NULL
        )
    """))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_local_inv_weight_log_spool ON local_inv_weight_log (spool_id)"))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS local_inv_weight_log"))
    await conn.execute(text("DROP TABLE IF EXISTS local_inv_spools"))
    await conn.execute(text("DROP TABLE IF EXISTS local_inv_materials"))
