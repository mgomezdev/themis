"""Durable catalog poll claims; independent of source metadata and local artifacts."""

from sqlalchemy import text

version = 77
name = "library_catalog_schedule"


async def up(conn):
    await conn.execute(
        text("""CREATE TABLE IF NOT EXISTS library_catalog_schedules (
        provider_plugin_id VARCHAR(64) PRIMARY KEY, next_due_at FLOAT NOT NULL)""")
    )


async def down(conn):
    await conn.execute(text("DROP TABLE IF EXISTS library_catalog_schedules"))
