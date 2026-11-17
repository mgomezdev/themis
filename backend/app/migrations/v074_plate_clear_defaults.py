"""Plate-clear defaults: queue_config.plate_clear_complete ("assume_good" | "ask"), plate_clear_failed ("assume_failed" | "ask")
— what a plate cleared WITHOUT results is taken to mean — and pause_after_failed_prints (3; 0 = off), plate_clear_since (the upgrade time: older plates never get a default): a printer's queue is
switched off after that many failed prints in a row, with printers.queue_paused_reason saying why. Idempotent."""
from sqlalchemy import text

version = 74
name = "plate_clear_defaults"

_COLUMNS = (
    ("queue_config", "plate_clear_complete", "VARCHAR(20) NOT NULL DEFAULT 'assume_good'"),
    ("queue_config", "plate_clear_failed", "VARCHAR(20) NOT NULL DEFAULT 'assume_failed'"),
    ("queue_config", "pause_after_failed_prints", "INTEGER NOT NULL DEFAULT 3"),
    ("queue_config", "plate_clear_since", "VARCHAR(40)"),
    ("printers", "queue_paused_reason", "VARCHAR(255)"),
)


async def _cols(conn, table):
    return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def up(conn) -> None:
    fresh = "plate_clear_since" not in await _cols(conn, "queue_config")
    for table, col, ddl in _COLUMNS:
        if col not in await _cols(conn, table):
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
    if fresh:
        # Plates that finished before this upgrade keep waiting for a person: only later ones get a default at plate clear
        # (otherwise the first clear would settle months of old unreported plates at once).
        from datetime import datetime, timezone
        await conn.execute(text("UPDATE queue_config SET plate_clear_since = :now"),
                           {"now": datetime.now(timezone.utc).isoformat()})


async def down(conn) -> None:
    for table, col, _ in _COLUMNS:
        if col in await _cols(conn, table):
            await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))
