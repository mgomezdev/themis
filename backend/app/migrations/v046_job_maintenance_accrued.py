"""Idempotency flag for maintenance accrual (BIZ-269): `jobs.maintenance_accrued`. Jobs that are already complete were counted by
the old inline path, so they are backfilled as accrued and a redelivered event can never count them again."""
from __future__ import annotations
from sqlalchemy import text

version = 46
name = "job_maintenance_accrued"


async def up(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    if "maintenance_accrued" not in cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN maintenance_accrued BOOLEAN NOT NULL DEFAULT 0"))
        await conn.execute(text("UPDATE jobs SET maintenance_accrued = 1 WHERE status = 'complete'"))


async def down(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    if "maintenance_accrued" in cols:
        await conn.execute(text("ALTER TABLE jobs DROP COLUMN maintenance_accrued"))
