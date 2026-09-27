"""Add Spoolman sync status tracking (interval, last success/attempt, last error)
so the status indicator and the Spoolman settings page can show real sync health."""
from __future__ import annotations
from sqlalchemy import text

version = 20
name = "spoolman_sync_status"


async def up(conn) -> None:
    cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(spoolman_config)"))).fetchall()}
    if "sync_interval_minutes" not in cols:
        await conn.execute(text(
            "ALTER TABLE spoolman_config ADD COLUMN sync_interval_minutes INTEGER NOT NULL DEFAULT 15"
        ))
    if "last_sync_at" not in cols:
        await conn.execute(text("ALTER TABLE spoolman_config ADD COLUMN last_sync_at TEXT"))
    if "last_attempt_at" not in cols:
        await conn.execute(text("ALTER TABLE spoolman_config ADD COLUMN last_attempt_at TEXT"))
    if "last_sync_error" not in cols:
        await conn.execute(text("ALTER TABLE spoolman_config ADD COLUMN last_sync_error TEXT"))
    if "last_sync_error_code" not in cols:
        await conn.execute(text("ALTER TABLE spoolman_config ADD COLUMN last_sync_error_code TEXT"))


async def down(conn) -> None:
    await conn.execute(text("ALTER TABLE spoolman_config DROP COLUMN last_sync_error_code"))
    await conn.execute(text("ALTER TABLE spoolman_config DROP COLUMN last_sync_error"))
    await conn.execute(text("ALTER TABLE spoolman_config DROP COLUMN last_attempt_at"))
    await conn.execute(text("ALTER TABLE spoolman_config DROP COLUMN last_sync_at"))
    await conn.execute(text("ALTER TABLE spoolman_config DROP COLUMN sync_interval_minutes"))
