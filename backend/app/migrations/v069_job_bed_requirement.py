"""Persist slicer plate footprints; old jobs/items retain their current behavior."""
from sqlalchemy import text

version = 69
name = "job_bed_requirement"


async def up(conn):
    for table in ("jobs", "project_items"):
        columns = {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}
        if "min_bed_mm" not in columns:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN min_bed_mm JSON"))


async def down(conn):
    for table in ("jobs", "project_items"):
        columns = {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}
        if "min_bed_mm" in columns:
            await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN min_bed_mm"))
