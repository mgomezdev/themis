"""Add bed_x_mm / bed_y_mm to printers for packing area selection."""
from __future__ import annotations
from sqlalchemy import text

version = 7
name = "printer_bed_size"


async def up(conn) -> None:
    await conn.execute(text("ALTER TABLE printers ADD COLUMN bed_x_mm REAL NOT NULL DEFAULT 256.0"))
    await conn.execute(text("ALTER TABLE printers ADD COLUMN bed_y_mm REAL NOT NULL DEFAULT 256.0"))


async def down(conn) -> None:
    # SQLite doesn't support DROP COLUMN before 3.35; recreate table
    await conn.execute(text("""
        CREATE TABLE printers_new AS
        SELECT id, name, printer_type, connection_config, awaiting_plate_clear,
               orca_printer_profiles, current_orca_printer_profile, enabled, queue_on,
               loaded_filaments, build_plate_type, no_snapshots_while_idle
        FROM printers
    """))
    await conn.execute(text("DROP TABLE printers"))
    await conn.execute(text("ALTER TABLE printers_new RENAME TO printers"))
