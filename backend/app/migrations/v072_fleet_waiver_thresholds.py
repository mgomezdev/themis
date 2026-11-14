"""Fleet-wide waiver thresholds: queue_config.waiver_flag_at / waiver_stop_at (0 = off) — the flag / stop limits for every
printer that sets none of its own (`printers.waiver_flag_at` / `waiver_stop_at`, 0 = use the fleet's). Idempotent."""
from sqlalchemy import text

version = 72
name = "fleet_waiver_thresholds"

_COLUMNS = ("waiver_flag_at", "waiver_stop_at")


async def up(conn) -> None:
    have = {r[1] for r in (await conn.execute(text("PRAGMA table_info(queue_config)"))).fetchall()}
    for col in _COLUMNS:
        if col not in have:
            await conn.execute(text(f"ALTER TABLE queue_config ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"))


async def down(conn) -> None:
    have = {r[1] for r in (await conn.execute(text("PRAGMA table_info(queue_config)"))).fetchall()}
    for col in _COLUMNS:
        if col in have:
            await conn.execute(text(f"ALTER TABLE queue_config DROP COLUMN {col}"))
