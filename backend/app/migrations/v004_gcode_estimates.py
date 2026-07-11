"""Add filament_grams and estimated_seconds to gcode_files."""
from __future__ import annotations
from sqlalchemy import text

version = 4
name = "gcode_estimates"


async def up(conn) -> None:
    try:
        await conn.execute(text("ALTER TABLE gcode_files ADD COLUMN filament_grams REAL"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE gcode_files ADD COLUMN estimated_seconds INTEGER"))
    except Exception:
        pass


async def down(conn) -> None:
    # SQLite: recreate table without the columns
    await conn.execute(text("""
        CREATE TABLE gcode_files_tmp (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL REFERENCES jobs(id),
            printer_id INTEGER NOT NULL REFERENCES printers(id),
            path TEXT NOT NULL
        )
    """))
    await conn.execute(text(
        "INSERT INTO gcode_files_tmp SELECT id, job_id, printer_id, path FROM gcode_files"
    ))
    await conn.execute(text("DROP TABLE gcode_files"))
    await conn.execute(text("ALTER TABLE gcode_files_tmp RENAME TO gcode_files"))
