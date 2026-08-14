"""Make "any" the canonical no-preference keyword for JobPrinterConfig's filament
ask. Backfills existing NULL/blank filament_type/filament_color to "any", then
tightens both columns to NOT NULL DEFAULT 'any'. Scoped to JobPrinterConfig only —
filament_map entries keep their own NULL semantics untouched."""
from __future__ import annotations
from sqlalchemy import text

version = 13
name = "filament_any_keyword"

# Explicit column list (not SELECT *) so the copy is correct regardless of the
# source table's physical column order.
_COLUMNS = (
    "id, job_id, printer_id, print_profile, filament_profile, filament_id, "
    "filament_type, filament_color, tool_index, filament_map, slice_failed, slice_error"
)


async def up(conn) -> None:
    info = (await conn.execute(text("PRAGMA table_info(job_printer_configs)"))).fetchall()
    notnull = {row[1]: row[3] for row in info}
    if notnull.get("filament_type") == 1 and notnull.get("filament_color") == 1:
        return  # already tightened (e.g. fresh schema created from current models.py)

    await conn.execute(text("""
        UPDATE job_printer_configs
        SET filament_type = 'any'
        WHERE filament_type IS NULL OR TRIM(filament_type) = ''
    """))
    await conn.execute(text("""
        UPDATE job_printer_configs
        SET filament_color = 'any'
        WHERE filament_color IS NULL OR TRIM(filament_color) = ''
    """))

    # SQLite can't ALTER COLUMN to add NOT NULL, so recreate the table.
    await conn.execute(text("""
        CREATE TABLE job_printer_configs_new (
            id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            printer_id INTEGER NOT NULL,
            print_profile VARCHAR(512) NOT NULL,
            filament_profile VARCHAR(512),
            filament_id INTEGER,
            filament_type VARCHAR(100) NOT NULL DEFAULT 'any',
            filament_color VARCHAR(20) NOT NULL DEFAULT 'any',
            tool_index INTEGER,
            filament_map JSON,
            slice_failed BOOLEAN NOT NULL DEFAULT 0,
            slice_error TEXT,
            PRIMARY KEY (id),
            FOREIGN KEY(job_id) REFERENCES jobs (id),
            FOREIGN KEY(printer_id) REFERENCES printers (id)
        )
    """))
    await conn.execute(text(
        f"INSERT INTO job_printer_configs_new ({_COLUMNS}) "
        f"SELECT {_COLUMNS} FROM job_printer_configs"
    ))
    await conn.execute(text("DROP TABLE job_printer_configs"))
    await conn.execute(text("ALTER TABLE job_printer_configs_new RENAME TO job_printer_configs"))


async def down(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE job_printer_configs_new (
            id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            printer_id INTEGER NOT NULL,
            print_profile VARCHAR(512) NOT NULL,
            filament_profile VARCHAR(512),
            filament_id INTEGER,
            filament_type VARCHAR(100),
            filament_color VARCHAR(20),
            tool_index INTEGER,
            filament_map JSON,
            slice_failed BOOLEAN NOT NULL DEFAULT 0,
            slice_error TEXT,
            PRIMARY KEY (id),
            FOREIGN KEY(job_id) REFERENCES jobs (id),
            FOREIGN KEY(printer_id) REFERENCES printers (id)
        )
    """))
    await conn.execute(text(
        f"INSERT INTO job_printer_configs_new ({_COLUMNS}) "
        f"SELECT {_COLUMNS} FROM job_printer_configs"
    ))
    await conn.execute(text("DROP TABLE job_printer_configs"))
    await conn.execute(text("ALTER TABLE job_printer_configs_new RENAME TO job_printer_configs"))
