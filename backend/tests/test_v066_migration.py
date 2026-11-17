"""v066 (BIZ-366 v2): job_spool_snapshots gains attempt_id in its unique key (table rebuilt, rows kept as ''), and the outbox
gains attempt_id; down() goes back to one snapshot per job/spool."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v037_inventory_deduction, v066_attempt_inventory as migration


async def _rows(conn, sql):
    return [tuple(r) for r in (await conn.execute(text(sql))).fetchall()]


async def test_the_rebuild_keeps_every_snapshot_and_allows_one_per_attempt(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            await conn.execute(text("CREATE TABLE jobs (id INTEGER PRIMARY KEY)"))
            await conn.execute(text("INSERT INTO jobs VALUES (1)"))
            await v037_inventory_deduction.up(conn)
            await conn.execute(text("INSERT INTO job_spool_snapshots (job_id, printer_id, provider, spool_ref, pre_weight_g, source, taken_at) "
                                    "VALUES (1, 2, 'spoolman', '11', 200.0, 'live', 't0')"))
            await migration.up(conn)
            await migration.up(conn)                                      # idempotent
            assert await _rows(conn, "SELECT job_id, spool_ref, pre_weight_g, attempt_id FROM job_spool_snapshots") == [(1, "11", 200.0, "")]
            await conn.execute(text("INSERT INTO job_spool_snapshots (job_id, attempt_id, provider, spool_ref, source, taken_at) "
                                    "VALUES (1, 'a2', 'spoolman', '11', 'live', 't1')"))                    # another attempt: allowed
            with pytest.raises(IntegrityError):
                await conn.execute(text("INSERT INTO job_spool_snapshots (job_id, attempt_id, provider, spool_ref, source, taken_at) "
                                        "VALUES (1, 'a2', 'spoolman', '11', 'live', 't2')"))
            assert "attempt_id" in {r[1] for r in await _rows(conn, "PRAGMA table_info(inventory_pending_writes)")}
            assert ("ix_job_spool_snapshots_job_id",) in await _rows(conn, "SELECT name FROM sqlite_master WHERE type='index'")
            await migration.down(conn)
            await migration.down(conn)
            assert await _rows(conn, "SELECT job_id, spool_ref, taken_at FROM job_spool_snapshots") == [(1, "11", "t1")]   # newest kept
            assert "attempt_id" not in {r[1] for r in await _rows(conn, "PRAGMA table_info(inventory_pending_writes)")}
    finally:
        await engine.dispose()
