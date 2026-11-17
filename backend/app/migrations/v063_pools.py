"""Pooled groups (BIZ-362): project_groups.max_parts / queue_position, jobs.pool_group_id. Idempotent."""
from __future__ import annotations
from sqlalchemy import text

version = 63
name = "pools"

_COLUMNS = (
    ("project_groups", "max_parts", "INTEGER"),
    ("project_groups", "queue_position", "FLOAT"),
    ("jobs", "pool_group_id", "INTEGER"),
)


async def _cols(conn, table):
    return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def up(conn) -> None:
    for table, col, ddl in _COLUMNS:
        if col not in await _cols(conn, table):
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_pool_group_id ON jobs (pool_group_id)"))


async def down(conn) -> None:
    await conn.execute(text("DROP INDEX IF EXISTS ix_jobs_pool_group_id"))
    for table, col, _ in reversed(_COLUMNS):
        if col in await _cols(conn, table):
            await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))
