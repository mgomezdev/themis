"""Printer part waivers (BIZ-365): part_waivers, queue_config.waive_after (default 2), printers.waiver_flag_at /
waiver_stop_at (0 = off) / test_print_job_id. Idempotent."""
from __future__ import annotations
from sqlalchemy import text

version = 62
name = "part_waivers"

_COLUMNS = (
    ("queue_config", "waive_after", "INTEGER NOT NULL DEFAULT 2"),
    ("printers", "waiver_flag_at", "INTEGER NOT NULL DEFAULT 0"),
    ("printers", "waiver_stop_at", "INTEGER NOT NULL DEFAULT 0"),
    ("printers", "test_print_job_id", "INTEGER"),
    ("printers", "test_print_waiver_id", "INTEGER"),
)


async def _cols(conn, table):
    return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS part_waivers (
            id INTEGER PRIMARY KEY,
            printer_id INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
            part_key VARCHAR(160) NOT NULL,
            file_id INTEGER,
            failures INTEGER NOT NULL DEFAULT 0,
            last_failure_at VARCHAR(32),
            created_at VARCHAR(32) NOT NULL,
            UNIQUE (printer_id, part_key)
        )"""))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_part_waivers_printer_id ON part_waivers (printer_id)"))
    for table, col, ddl in _COLUMNS:
        if col not in await _cols(conn, table):
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))


async def down(conn) -> None:
    for table, col, _ in reversed(_COLUMNS):
        if col in await _cols(conn, table):
            await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))
    await conn.execute(text("DROP TABLE IF EXISTS part_waivers"))
