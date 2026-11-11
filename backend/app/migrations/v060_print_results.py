"""Print results (BIZ-364): job_item_failures.printer_id / created_at / resolution (the failure log), project_items.quantity_held,
queue_config.part_retry_limit (default 2). Idempotent."""
from __future__ import annotations
from sqlalchemy import text

version = 60
name = "print_results"

_COLUMNS = (
    ("job_item_failures", "printer_id", "INTEGER"),
    ("job_item_failures", "created_at", "VARCHAR(32)"),
    ("job_item_failures", "resolution", "VARCHAR(20)"),
    ("project_items", "quantity_held", "INTEGER NOT NULL DEFAULT 0"),
    ("queue_config", "part_retry_limit", "INTEGER NOT NULL DEFAULT 2"),
)


async def _cols(conn, table: str) -> set[str]:
    return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def up(conn) -> None:
    for table, col, ddl in _COLUMNS:
        if col not in await _cols(conn, table):
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))


async def down(conn) -> None:
    for table, col, _ in reversed(_COLUMNS):
        if col in await _cols(conn, table):
            await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))
