"""Deduction model tables (BIZ-218): job_spool_snapshots, inventory_pending_writes (the outbox), inventory_spool_status, and
`jobs.deduction_note`. Jobs already `printing` at upgrade time simply have no snapshot: their completion takes one then."""
from __future__ import annotations
from sqlalchemy import text

version = 37
name = "inventory_deduction"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS job_spool_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            printer_id INTEGER,
            provider VARCHAR(64) NOT NULL,
            spool_ref VARCHAR(128) NOT NULL,
            pre_weight_g FLOAT,
            source VARCHAR(16) NOT NULL,
            taken_at VARCHAR(32) NOT NULL,
            CONSTRAINT ux_job_spool_snapshots UNIQUE (job_id, provider, spool_ref)
        )
    """))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_spool_snapshots_job_id ON job_spool_snapshots (job_id)"))
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS inventory_pending_writes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider VARCHAR(64) NOT NULL,
            spool_ref VARCHAR(128) NOT NULL,
            target_g FLOAT NOT NULL,
            job_id INTEGER,
            printer_id INTEGER,
            source VARCHAR(24) NOT NULL,
            created_at VARCHAR(32) NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_attempt_at VARCHAR(32),
            last_error TEXT,
            status VARCHAR(16) NOT NULL DEFAULT 'pending'
        )
    """))
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_inventory_pending_writes_spool ON inventory_pending_writes (provider, spool_ref, status)"))
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS inventory_spool_status (
            provider VARCHAR(64) NOT NULL,
            spool_ref VARCHAR(128) NOT NULL,
            tracking VARCHAR(16) NOT NULL DEFAULT 'suspended',
            reason TEXT NOT NULL,
            since VARCHAR(32) NOT NULL,
            job_id INTEGER,
            PRIMARY KEY (provider, spool_ref)
        )
    """))
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    if "deduction_note" not in cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN deduction_note TEXT"))


async def down(conn) -> None:
    await conn.execute(text("ALTER TABLE jobs DROP COLUMN deduction_note"))
    await conn.execute(text("DROP TABLE IF EXISTS inventory_spool_status"))
    await conn.execute(text("DROP TABLE IF EXISTS inventory_pending_writes"))
    await conn.execute(text("DROP TABLE IF EXISTS job_spool_snapshots"))
