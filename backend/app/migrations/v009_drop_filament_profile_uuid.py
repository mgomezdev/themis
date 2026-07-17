"""Drop filament_profile_uuid from project_items — column is dead (no code reads/writes it)."""
from __future__ import annotations
from sqlalchemy import text

version = 9
name = "drop_filament_profile_uuid"


async def up(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(project_items)"))).fetchall()}
    if "filament_profile_uuid" in cols:
        await conn.execute(text("ALTER TABLE project_items DROP COLUMN filament_profile_uuid"))
