"""Durable event outbox (BIZ-249): `event_outbox` (one row per durable envelope, written in the publisher's transaction) and
`event_deliveries` (one row per subscriber that must receive it, with retry state)."""
from __future__ import annotations
from sqlalchemy import text

version = 45
name = "event_outbox"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS event_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id VARCHAR(32) NOT NULL UNIQUE,
            dedup_key VARCHAR(255) UNIQUE,
            name VARCHAR(128) NOT NULL,
            schema_version INTEGER NOT NULL,
            source VARCHAR(64) NOT NULL,
            occurred_at VARCHAR(32) NOT NULL,
            envelope JSON NOT NULL,
            created_at VARCHAR(32) NOT NULL
        )
    """))
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS event_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outbox_id INTEGER NOT NULL REFERENCES event_outbox(id) ON DELETE CASCADE,
            subscriber VARCHAR(160) NOT NULL,
            status VARCHAR(12) NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at VARCHAR(32) NOT NULL,
            last_error TEXT,
            last_attempt_at VARCHAR(32),
            delivered_at VARCHAR(32),
            CONSTRAINT ux_event_deliveries UNIQUE (outbox_id, subscriber)
        )
    """))
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_event_deliveries_due ON event_deliveries (status, next_attempt_at)"))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS event_deliveries"))
    await conn.execute(text("DROP TABLE IF EXISTS event_outbox"))
