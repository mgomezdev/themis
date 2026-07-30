"""Add maintenance tracking: items/triggers/per-printer state, plus lifetime counters on printers."""
from __future__ import annotations
from sqlalchemy import text

version = 11
name = "maintenance_tracking"


async def up(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
    if "lifetime_job_count" not in cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN lifetime_job_count INTEGER NOT NULL DEFAULT 0"))
    if "lifetime_print_seconds" not in cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN lifetime_print_seconds INTEGER NOT NULL DEFAULT 0"))

    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS maintenance_items (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT    NOT NULL,
            scope          TEXT    NOT NULL DEFAULT 'general',
            machine_vendor TEXT,
            machine_model  TEXT,
            enabled        BOOLEAN NOT NULL DEFAULT 1,
            notes          TEXT,
            created_at     TEXT    NOT NULL,
            updated_at     TEXT    NOT NULL
        )
    """))

    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS maintenance_triggers (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            maintenance_item_id  INTEGER NOT NULL
                REFERENCES maintenance_items(id) ON DELETE CASCADE,
            trigger_type         TEXT    NOT NULL,
            amount               REAL    NOT NULL,
            unit                 TEXT
        )
    """))

    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS printer_maintenance_state (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            printer_id             INTEGER NOT NULL
                REFERENCES printers(id) ON DELETE CASCADE,
            maintenance_item_id    INTEGER NOT NULL
                REFERENCES maintenance_items(id) ON DELETE CASCADE,
            last_done_at           TEXT    NOT NULL,
            baseline_job_count     INTEGER NOT NULL DEFAULT 0,
            baseline_print_seconds INTEGER NOT NULL DEFAULT 0,
            UNIQUE(printer_id, maintenance_item_id)
        )
    """))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS printer_maintenance_state"))
    await conn.execute(text("DROP TABLE IF EXISTS maintenance_triggers"))
    await conn.execute(text("DROP TABLE IF EXISTS maintenance_items"))
    # SQLite doesn't support DROP COLUMN before 3.35; recreate printers table
    await conn.execute(text("""
        CREATE TABLE printers_new AS
        SELECT id, name, printer_type, connection_config, awaiting_plate_clear,
               orca_printer_profiles, current_orca_printer_profile, enabled, queue_on,
               loaded_filaments, build_plate_type, no_snapshots_while_idle, bed_x_mm, bed_y_mm
        FROM printers
    """))
    await conn.execute(text("DROP TABLE printers"))
    await conn.execute(text("ALTER TABLE printers_new RENAME TO printers"))
