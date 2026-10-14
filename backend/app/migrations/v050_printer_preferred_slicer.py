"""Per-printer preferred slicing provider (BIZ-291): `printers.preferred_slicer_plugin_id`, a `gcode.produce` plugin id. NULL = the
capability default (the selection row). The id is deliberately not a foreign key: a plugin can be disabled or removed, and the
printer keeps naming it (the job then blocks as `preference_dormant` instead of silently using another slicer, BIZ-250)."""
from __future__ import annotations
from sqlalchemy import text

version = 50
name = "printer_preferred_slicer"


async def up(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
    if "preferred_slicer_plugin_id" not in cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN preferred_slicer_plugin_id VARCHAR(64)"))


async def down(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
    if "preferred_slicer_plugin_id" in cols:
        await conn.execute(text("ALTER TABLE printers DROP COLUMN preferred_slicer_plugin_id"))
