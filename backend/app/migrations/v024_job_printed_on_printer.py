"""jobs.printed_on_printer_id: the printer a job actually ran on, kept after assigned_printer_id is cleared.

assigned_printer_id is nulled when a job fails or is cancelled, so fleet analytics could not attribute
failures to a printer. This column is set when the job is committed to a printer and never cleared
(nulled by delete_printer when that printer is deleted). Deliberately a plain INTEGER, not a FK: SQLite
cannot DROP COLUMN a column carrying a foreign key, which would break down()."""
from __future__ import annotations
from sqlalchemy import text

version = 24
name = "job_printed_on_printer"


async def up(conn) -> None:
    cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    if "printed_on_printer_id" not in cols:
        await conn.execute(text(
            "ALTER TABLE jobs ADD COLUMN printed_on_printer_id INTEGER"
        ))
        # Best effort for history: jobs that still carry their printer (e.g. completed ones).
        await conn.execute(text(
            "UPDATE jobs SET printed_on_printer_id = assigned_printer_id "
            "WHERE assigned_printer_id IS NOT NULL AND status IN ('complete', 'printing', 'paused', 'failed', 'cancelled')"
        ))


async def down(conn) -> None:
    await conn.execute(text("ALTER TABLE jobs DROP COLUMN printed_on_printer_id"))
