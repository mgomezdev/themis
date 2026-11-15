import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v069_job_bed_requirement as bed, v070_target_intent as target, v071_slicer_sends as sends


@pytest.mark.parametrize("migration",[bed,target,sends])
async def test_slicer_migration_up_down_is_idempotent(migration):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for table in ("jobs","project_items","project_groups","job_printer_configs","job_model_targets","project_group_targets"):
            await conn.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)"))
            await conn.execute(text(f"INSERT INTO {table} (id) VALUES (1)"))
        await migration.up(conn)
        await migration.up(conn)
        if migration is sends:
            await conn.execute(text("INSERT INTO slicer_sends (send_id,source_app,request_hash,created_at) VALUES ('s','orcaslicer','h','now')"))
        await migration.down(conn)
        await migration.down(conn)
        for table in ("jobs","project_items","project_groups","job_printer_configs","job_model_targets","project_group_targets"):
            assert (await conn.execute(text(f"SELECT id FROM {table}"))).scalar() == 1
    await engine.dispose()
