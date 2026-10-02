"""jobs.not_before (don't start before this UTC instant) and printers.quiet_start/quiet_end (server-local HH:MM window when a printer won't start new jobs)."""
from __future__ import annotations
from sqlalchemy import text

version = 29
name = "job_schedule"


async def up(conn) -> None:
    job_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    if "not_before" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN not_before TEXT"))
    printer_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
    if "quiet_start" not in printer_cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN quiet_start TEXT"))
    if "quiet_end" not in printer_cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN quiet_end TEXT"))


async def down(conn) -> None:
    await conn.execute(text("ALTER TABLE printers DROP COLUMN quiet_end"))
    await conn.execute(text("ALTER TABLE printers DROP COLUMN quiet_start"))
    await conn.execute(text("ALTER TABLE jobs DROP COLUMN not_before"))
