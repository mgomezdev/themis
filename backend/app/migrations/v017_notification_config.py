"""Add notification_config table."""
from __future__ import annotations
from sqlalchemy import text

version = 17
name = "notification_config"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS notification_config (
            id INTEGER PRIMARY KEY,
            ntfy_enabled BOOLEAN NOT NULL DEFAULT 0,
            ntfy_server_url TEXT,
            ntfy_topic TEXT,
            ntfy_events TEXT NOT NULL DEFAULT '[]',
            discord_enabled BOOLEAN NOT NULL DEFAULT 0,
            discord_webhook_url TEXT,
            discord_events TEXT NOT NULL DEFAULT '[]',
            email_enabled BOOLEAN NOT NULL DEFAULT 0,
            email_host TEXT,
            email_port INTEGER,
            email_username TEXT,
            email_password TEXT,
            email_from_addr TEXT,
            email_to_addrs TEXT NOT NULL DEFAULT '[]',
            email_events TEXT NOT NULL DEFAULT '[]'
        )
    """))
    await conn.execute(text("""
        INSERT OR IGNORE INTO notification_config
            (id, ntfy_enabled, ntfy_events, discord_enabled, discord_events,
             email_enabled, email_to_addrs, email_events)
        VALUES (1, 0, '[]', 0, '[]', 0, '[]', '[]')
    """))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS notification_config"))
