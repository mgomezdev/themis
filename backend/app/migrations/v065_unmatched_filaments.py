"""Persist the filament rows that caused a fleet-wide job block."""
from sqlalchemy import text

version = 65
name = "unmatched_filaments"


async def up(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    if "unmatched_filaments" not in cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN unmatched_filaments JSON"))


async def down(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    if "unmatched_filaments" in cols:
        await conn.execute(text("ALTER TABLE jobs DROP COLUMN unmatched_filaments"))
