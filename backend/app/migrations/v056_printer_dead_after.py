"""queue_config.printer_dead_after_minutes (printer liveness): minutes a printer may stay offline before it is declared dead, its
running job failed and requeued at the top. Default 30; 0 = never. Idempotent."""
from __future__ import annotations
from sqlalchemy import text

version = 56
name = "printer_dead_after"


async def up(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(queue_config)"))).fetchall()}
    if "printer_dead_after_minutes" not in cols:
        await conn.execute(text("ALTER TABLE queue_config ADD COLUMN printer_dead_after_minutes INTEGER NOT NULL DEFAULT 30"))


async def down(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(queue_config)"))).fetchall()}
    if "printer_dead_after_minutes" in cols:
        await conn.execute(text("ALTER TABLE queue_config DROP COLUMN printer_dead_after_minutes"))
