"""Job details (job events + details design, §3): `job_details`, one row per (job, field, origin). Written by printer plugins through
the job reporter in the same transaction as the state change, and by other plugins through the details writer. Cascades with the job."""
from __future__ import annotations
from sqlalchemy import text

version = 55
name = "job_details"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS job_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            field VARCHAR(64) NOT NULL,
            origin VARCHAR(64) NOT NULL,
            status VARCHAR(12) NOT NULL DEFAULT 'ok',
            value JSON,
            updated_at VARCHAR(32) NOT NULL,
            CONSTRAINT ux_job_details UNIQUE (job_id, field, origin)
        )
    """))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_details_job_id ON job_details (job_id)"))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS job_details"))
