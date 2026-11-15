"""printers.slice_overrides: per-printer slicer setting overrides (curated keys -> value, e.g. {"brim_type": "outer_only"}), applied
under the job's own overrides and the project file's own preset changes. NULL = none. Idempotent."""
from __future__ import annotations
from sqlalchemy import text

version = 57
name = "printer_slice_overrides"


async def up(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
    if "slice_overrides" not in cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN slice_overrides JSON"))


async def down(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
    if "slice_overrides" in cols:
        await conn.execute(text("ALTER TABLE printers DROP COLUMN slice_overrides"))
