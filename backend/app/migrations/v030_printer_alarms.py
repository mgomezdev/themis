"""printer_alarms (alarm history + acknowledge) and queue_config.alarm_min_severity (webhook/notification filter)."""
from __future__ import annotations
from sqlalchemy import text

version = 30
name = "printer_alarms"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS printer_alarms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            printer_id INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
            code VARCHAR(80) NOT NULL,
            severity VARCHAR(10) NOT NULL,
            message TEXT NOT NULL,
            source VARCHAR(40),
            help_url VARCHAR(300),
            first_seen VARCHAR(32) NOT NULL,
            last_seen VARCHAR(32) NOT NULL,
            resolved_at VARCHAR(32),
            acknowledged_at VARCHAR(32)
        )
    """))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_printer_alarms_printer_id ON printer_alarms (printer_id)"))
    cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(queue_config)"))).fetchall()}
    if "alarm_min_severity" not in cols:
        await conn.execute(text("ALTER TABLE queue_config ADD COLUMN alarm_min_severity VARCHAR(10) NOT NULL DEFAULT 'warning'"))


async def down(conn) -> None:
    await conn.execute(text("ALTER TABLE queue_config DROP COLUMN alarm_min_severity"))
    await conn.execute(text("DROP TABLE IF EXISTS printer_alarms"))
