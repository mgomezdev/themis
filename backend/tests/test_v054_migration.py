"""v054: jobs.id is never reused (AUTOINCREMENT). The rebuild keeps every row, default, index and child foreign key."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v054_jobs_autoincrement as mig
from app.migrations.runner import _CREATE_TABLE, _MIGRATIONS, run_migrations
from tests.v032_fixture import build_v032_fixture_db


async def _migrate_to(conn, max_version: int) -> None:
    await conn.execute(text(_CREATE_TABLE))
    applied = {r[0] for r in (await conn.execute(text("SELECT version FROM schema_migrations"))).fetchall()}
    for m in _MIGRATIONS:
        if m.version <= max_version and m.version not in applied:
            await m.up(conn)
            await conn.execute(text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"), {"v": m.version, "n": m.name})


async def _ddl(conn) -> str:
    return (await conn.execute(text("SELECT sql FROM sqlite_master WHERE name='jobs'"))).scalar_one()


async def _to_plain_pk(conn) -> None:
    """Rebuild jobs with a plain `INTEGER PRIMARY KEY` (what existing databases have), keeping rows and indexes."""
    ddl = await _ddl(conn)
    assert "AUTOINCREMENT" in ddl.upper()
    cols = ", ".join(f'"{r[1]}"' for r in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall())
    extras = [r[0] for r in (await conn.execute(text("SELECT sql FROM sqlite_master WHERE tbl_name='jobs' AND type='index' AND sql IS NOT NULL"))).fetchall()]
    await conn.execute(text("PRAGMA defer_foreign_keys=ON"))
    await conn.execute(text(ddl.replace("CREATE TABLE jobs", "CREATE TABLE jobs_old", 1).replace(" AUTOINCREMENT", "")))
    await conn.execute(text(f"INSERT INTO jobs_old ({cols}) SELECT {cols} FROM jobs"))
    await conn.execute(text("DROP TABLE jobs"))
    await conn.execute(text("ALTER TABLE jobs_old RENAME TO jobs"))
    for sql in extras:
        await conn.execute(text(sql))
    assert "AUTOINCREMENT" not in (await _ddl(conn)).upper()


@pytest.fixture
async def v053_db(tmp_path):
    path = await build_v032_fixture_db(tmp_path / "v032.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await _migrate_to(conn, 53)
        await _to_plain_pk(conn)       # the production shape before v054 (create_all now emits AUTOINCREMENT)
    yield engine
    await engine.dispose()


async def test_rebuild_keeps_rows_children_and_indexes_and_stops_id_reuse(v053_db):
    async with v053_db.begin() as conn:
        assert "AUTOINCREMENT" not in (await _ddl(conn)).upper()
        before = [dict(r) for r in (await conn.execute(text("SELECT * FROM jobs ORDER BY id"))).mappings().all()]
        children = (await conn.execute(text("SELECT COUNT(*) FROM job_printer_configs"))).scalar_one()
        idx_before = {r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='jobs'"))).fetchall()}
        assert before, "the fixture must contain jobs"
        await run_migrations(conn)
    async with v053_db.begin() as conn:
        assert "AUTOINCREMENT" in (await _ddl(conn)).upper()
        after = [dict(r) for r in (await conn.execute(text("SELECT * FROM jobs ORDER BY id"))).mappings().all()]
        assert after == before                                              # every column of every row, defaults included
        assert (await conn.execute(text("SELECT COUNT(*) FROM job_printer_configs"))).scalar_one() == children
        assert (await conn.execute(text("PRAGMA foreign_key_check"))).fetchall() == []
        idx_after = {r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='jobs'"))).fetchall()}
        assert idx_before <= idx_after
        top = max(r["id"] for r in before)
        seq = (await conn.execute(text("SELECT seq FROM sqlite_sequence WHERE name='jobs'"))).scalar_one()
        assert seq == top
        # delete the newest job: its id must not come back
        template = dict(before[-1])
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.execute(text("DELETE FROM jobs WHERE id=:i"), {"i": top})
        template.pop("id")
        cols = ", ".join(template)
        binds = ", ".join(f":{c}" for c in template)
        await conn.execute(text(f"INSERT INTO jobs ({cols}) VALUES ({binds})"), template)
        new_id = (await conn.execute(text("SELECT MAX(id) FROM jobs"))).scalar_one()
        assert new_id == top + 1


async def test_up_is_idempotent(v053_db):
    async with v053_db.begin() as conn:
        await mig.up(conn)
        first = await _ddl(conn)
        await mig.up(conn)
        assert await _ddl(conn) == first
        assert first.upper().count("AUTOINCREMENT") == 1


async def test_rewrite_rejects_a_ddl_it_does_not_understand():
    with pytest.raises(RuntimeError):
        mig._rewrite("CREATE TABLE other (id INTEGER)")
    with pytest.raises(RuntimeError):
        mig._rewrite("CREATE TABLE jobs (name TEXT)")
