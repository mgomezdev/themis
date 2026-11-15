"""printers.default_process_profile + printers.single_colour_only (BIZ-358): the process preset a "Printer default" job config
slices with (NULL = none set), and a flag that keeps multicolour work off the printer. Idempotent."""
from __future__ import annotations
from sqlalchemy import text

version = 57
name = "printer_slicing_defaults"


async def up(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
    if "default_process_profile" not in cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN default_process_profile VARCHAR(512)"))
    if "single_colour_only" not in cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN single_colour_only BOOLEAN NOT NULL DEFAULT 0"))


async def down(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
    for col in ("default_process_profile", "single_colour_only"):
        if col in cols:
            await conn.execute(text(f"ALTER TABLE printers DROP COLUMN {col}"))
