"""Weight-conflict guard for the deduction outbox (BIZ-198): the weight a pending write was computed from and, when the
provider's weight turned out to have changed meanwhile, the weight found there."""
from __future__ import annotations
from sqlalchemy import text

version = 41
name = "pending_write_conflict"

_COLUMNS = {"pre_weight_g": "FLOAT", "conflict_current_g": "FLOAT"}


async def _columns(conn) -> set[str]:
    return {r[1] for r in (await conn.execute(text("PRAGMA table_info(inventory_pending_writes)"))).fetchall()}


async def up(conn) -> None:
    have = await _columns(conn)
    for col, ddl in _COLUMNS.items():
        if col not in have:
            await conn.execute(text(f"ALTER TABLE inventory_pending_writes ADD COLUMN {col} {ddl}"))


async def down(conn) -> None:
    have = await _columns(conn)
    for col in _COLUMNS:
        if col in have:
            await conn.execute(text(f"ALTER TABLE inventory_pending_writes DROP COLUMN {col}"))
