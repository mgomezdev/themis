"""job_item_failures.requeue_pending (GH #227): failed quantity committed with the plate results but not yet repacked, so an
interruption between the results and the repack can be resumed instead of losing the parts."""
from sqlalchemy import text

version = 83
name = "requeue_pending"


async def up(conn):
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(job_item_failures)"))).fetchall()}
    if "requeue_pending" not in cols:
        await conn.execute(text("ALTER TABLE job_item_failures ADD COLUMN requeue_pending INTEGER NOT NULL DEFAULT 0"))


async def down(conn):
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(job_item_failures)"))).fetchall()}
    if "requeue_pending" in cols:
        await conn.execute(text("ALTER TABLE job_item_failures DROP COLUMN requeue_pending"))
