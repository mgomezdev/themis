"""spoolman_config: low-inventory alert thresholds (default + per-filament overrides) and the alerted-spool set."""
from __future__ import annotations
from sqlalchemy import text

version = 26
name = "spool_low_stock"


async def up(conn) -> None:
    cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(spoolman_config)"))).fetchall()}
    if "low_stock_default_g" not in cols:
        await conn.execute(text("ALTER TABLE spoolman_config ADD COLUMN low_stock_default_g REAL"))
    if "low_stock_overrides" not in cols:
        await conn.execute(text("ALTER TABLE spoolman_config ADD COLUMN low_stock_overrides JSON"))
    if "low_stock_alerted" not in cols:
        await conn.execute(text("ALTER TABLE spoolman_config ADD COLUMN low_stock_alerted JSON"))


async def down(conn) -> None:
    await conn.execute(text("ALTER TABLE spoolman_config DROP COLUMN low_stock_alerted"))
    await conn.execute(text("ALTER TABLE spoolman_config DROP COLUMN low_stock_overrides"))
    await conn.execute(text("ALTER TABLE spoolman_config DROP COLUMN low_stock_default_g"))
