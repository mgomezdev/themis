"""Atomic slicer sends: one durable identity for job or project imports and later updates."""
from sqlalchemy import text

version = 71
name = "slicer_sends"


async def up(conn):
    await conn.execute(text("""CREATE TABLE IF NOT EXISTS slicer_sends (
        send_id VARCHAR(36) PRIMARY KEY, source_app VARCHAR(32) NOT NULL,
        request_hash VARCHAR(64) NOT NULL, result JSON, created_at VARCHAR(40) NOT NULL)"""))
    for table in ("jobs","project_items"):
        columns = {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}
        for column,sqltype in (("source_app","VARCHAR(32)"),("send_id","VARCHAR(36)")):
            if column not in columns:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))


async def down(conn):
    for table in ("jobs","project_items"):
        columns = {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}
        for column in ("source_app","send_id"):
            if column in columns:
                await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
    await conn.execute(text("DROP TABLE IF EXISTS slicer_sends"))
