"""v055: `job_details` exists, is unique per (job, field, origin), cascades with the job, and rolls back."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v055_job_details as mig
from app.migrations.runner import _CREATE_TABLE, _MIGRATIONS
from tests.v032_fixture import build_v032_fixture_db


@pytest.fixture
async def db(tmp_path):
    path = await build_v032_fixture_db(tmp_path / "v032.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS job_details"))
        await conn.execute(text(_CREATE_TABLE))
    yield engine
    await engine.dispose()


async def test_up_creates_a_unique_cascading_table_and_down_drops_it(db):
    async with db.begin() as conn:
        await mig.up(conn)
        await mig.up(conn)                                                       # idempotent
        job = (await conn.execute(text("SELECT id FROM jobs LIMIT 1"))).scalar_one()
        ins = ("INSERT INTO job_details (job_id, field, origin, status, value, updated_at) "
               "VALUES (:j, 'f', 'o', 'ok', '1', '2026-10-20T00:00:00Z')")
        await conn.execute(text(ins), {"j": job})
    with pytest.raises(IntegrityError):
        async with db.begin() as conn:
            await conn.execute(text(ins), {"j": job})                          # same (job, field, origin)
    async with db.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.execute(text("DELETE FROM job_printer_configs WHERE job_id=:j"), {"j": job})
        await conn.execute(text("DELETE FROM job_model_targets WHERE job_id=:j"), {"j": job})
        await conn.execute(text("DELETE FROM jobs WHERE id=:j"), {"j": job})
        assert (await conn.execute(text("SELECT COUNT(*) FROM job_details"))).scalar_one() == 0     # cascaded
        await mig.down(conn)
        assert (await conn.execute(text("SELECT name FROM sqlite_master WHERE name='job_details'"))).first() is None


def test_it_is_registered_after_v054():
    assert mig in _MIGRATIONS and mig.version == 55
