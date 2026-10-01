"""project_payments: one row per payment received, with the date it arrived and how it was paid.

Existing projects keep what was recorded as `amount_paid` as a single opening payment (dated the project's
creation day, which is the day reporting already bucketed it under), so no money history is lost or shifted."""
from __future__ import annotations
from sqlalchemy import text

version = 24
name = "project_payments"

OPENING_NOTE = "Opening balance (migrated from amount paid)"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS project_payments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            amount      REAL    NOT NULL,
            received_on TEXT    NOT NULL,
            method      VARCHAR(20) NOT NULL DEFAULT 'other',
            note        TEXT,
            created_at  VARCHAR(32) NOT NULL DEFAULT ''
        )
    """))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_project_payments_project_id ON project_payments (project_id)"))
    existing = (await conn.execute(text("SELECT COUNT(*) FROM project_payments"))).scalar()
    if not existing:
        await conn.execute(text("""
            INSERT INTO project_payments (project_id, amount, received_on, method, note, created_at)
            SELECT id, amount_paid, substr(created_at, 1, 10), 'other', :note, created_at
            FROM projects WHERE amount_paid IS NOT NULL AND amount_paid > 0
        """), {"note": OPENING_NOTE})


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS project_payments"))
