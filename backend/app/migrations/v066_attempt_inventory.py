"""Inventory per physical print attempt (BIZ-366 v2): a failed or requeued print is settled on its own and a reprint deducts on top.

`job_spool_snapshots` gains `attempt_id` (NOT NULL, '' for rows written before attempts existed) and its unique key becomes
(job_id, attempt_id, provider, spool_ref). SQLite can't change a table constraint in place, so the table is rebuilt; nothing references
it (it is a child of `jobs` only), so dropping the old copy cascades nothing. `inventory_pending_writes` gains a nullable `attempt_id`.
Idempotent."""
from __future__ import annotations

from sqlalchemy import text

version = 66
name = "attempt_inventory"
reshapes_schema = True   # rebuilds job_spool_snapshots (tests/test_migrations.py skips it when it looks for a remove-only rollback)

_SNAPSHOTS = """
    CREATE TABLE {name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        printer_id INTEGER,
        provider VARCHAR(64) NOT NULL,
        spool_ref VARCHAR(128) NOT NULL,
        pre_weight_g FLOAT,
        source VARCHAR(16) NOT NULL,
        taken_at VARCHAR(32) NOT NULL,
        {attempt}
        CONSTRAINT ux_job_spool_snapshots UNIQUE ({key})
    )"""
_COLS = "id, job_id, printer_id, provider, spool_ref, pre_weight_g, source, taken_at"


async def _cols(conn, table):
    return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def _rebuild(conn, *, with_attempt: bool) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS job_spool_snapshots_new"))
    await conn.execute(text(_SNAPSHOTS.format(
        name="job_spool_snapshots_new",
        attempt="attempt_id VARCHAR(200) NOT NULL DEFAULT ''," if with_attempt else "",
        key="job_id, attempt_id, provider, spool_ref" if with_attempt else "job_id, provider, spool_ref")))
    if with_attempt:
        await conn.execute(text(f"INSERT INTO job_spool_snapshots_new ({_COLS}, attempt_id) SELECT {_COLS}, '' FROM job_spool_snapshots"))
    else:   # back to one snapshot per job/spool: keep the newest attempt's
        await conn.execute(text(f"""INSERT INTO job_spool_snapshots_new ({_COLS}) SELECT {_COLS} FROM job_spool_snapshots
                                    WHERE id IN (SELECT MAX(id) FROM job_spool_snapshots GROUP BY job_id, provider, spool_ref)"""))
    await conn.execute(text("DROP TABLE job_spool_snapshots"))
    await conn.execute(text("ALTER TABLE job_spool_snapshots_new RENAME TO job_spool_snapshots"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_spool_snapshots_job_id ON job_spool_snapshots (job_id)"))


async def up(conn) -> None:
    if "attempt_id" not in await _cols(conn, "job_spool_snapshots"):
        await _rebuild(conn, with_attempt=True)
    if "attempt_id" not in await _cols(conn, "inventory_pending_writes"):
        await conn.execute(text("ALTER TABLE inventory_pending_writes ADD COLUMN attempt_id VARCHAR(200)"))


async def down(conn) -> None:
    if "attempt_id" in await _cols(conn, "inventory_pending_writes"):
        await conn.execute(text("ALTER TABLE inventory_pending_writes DROP COLUMN attempt_id"))
    if "attempt_id" in await _cols(conn, "job_spool_snapshots"):
        await _rebuild(conn, with_attempt=False)
