"""v058: job_model_targets.model_uuid becomes nullable (Any printer); rows and ids survive; idempotent; down drops Any-printer rows."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v058_any_printer_target as mig
from app.migrations.runner import run_migrations


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    async with engine.begin() as conn:
        await run_migrations(conn)
        await mig.down(conn)                      # back to the v057 shape (NOT NULL) to exercise up() on real data
    yield engine
    await engine.dispose()


async def _notnull(conn) -> int:
    return next(r[3] for r in (await conn.execute(text("PRAGMA table_info(job_model_targets)"))).fetchall() if r[1] == "model_uuid")


async def test_up_relaxes_not_null_keeps_rows_and_down_restores_it(db):
    async with db.begin() as conn:
        assert await _notnull(conn) == 1
        # (this bare engine runs without the app's foreign-key pragma, so target rows need no parent rows)
        await conn.execute(text("INSERT INTO job_model_targets (id, job_id, model_uuid, print_profile, ask_type) VALUES (7, 1, 'm1', 'P', 'PLA')"))
        await mig.up(conn)
        await mig.up(conn)
        assert await _notnull(conn) == 0
        assert (await conn.execute(text("SELECT id, model_uuid, ask_type FROM job_model_targets"))).fetchall() == [(7, "m1", "PLA")]
        await conn.execute(text("INSERT INTO job_model_targets (id, job_id, model_uuid, print_profile) VALUES (8, 1, NULL, 'P')"))
        await mig.down(conn)
        await mig.down(conn)
        assert await _notnull(conn) == 1
        assert [r[0] for r in (await conn.execute(text("SELECT id FROM job_model_targets"))).fetchall()] == [7]
