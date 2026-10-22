"""v036 adds provider-namespaced refs next to the legacy ones (BIZ-217): run on the phase-0 v032 fixture, re-read every row,
idempotent, `down()`, and the fresh-DB path."""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base
from app.migrations import v036_inventory_refs
from app.migrations.runner import run_migrations
from tests.v032_fixture import FIXTURE_FACTS, build_v032_fixture_db

TABLES = ("job_printer_configs", "job_model_targets", "project_items")


@pytest.fixture
async def migrated(tmp_path):
    path = await build_v032_fixture_db(tmp_path / "v032.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await run_migrations(conn)
    conn = await engine.connect()
    yield conn
    await conn.close()
    await engine.dispose()


async def _cols(conn, table):
    return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def test_every_filament_id_table_gains_and_backfills_the_namespaced_pair(migrated):
    for table, filament_id in FIXTURE_FACTS["filament_id_by_table"].items():
        row = (await migrated.execute(text(f"SELECT filament_id, material_provider, material_ref FROM {table}"))).one()
        assert tuple(row) == (filament_id, "spoolman", str(filament_id)), table


async def test_slots_with_a_spool_get_an_inventory_binding_and_empty_slots_do_not(migrated):
    slots = json.loads((await migrated.execute(text("SELECT loaded_filaments FROM printers"))).scalar_one())
    assert [s.get("inventory") for s in slots] == [
        {"provider": "spoolman", "spool_ref": "1"}, {"provider": "spoolman", "spool_ref": "2"}, None]
    assert [s["spoolman_spool_id"] for s in slots] == FIXTURE_FACTS["slot_spool_ids"]          # legacy key untouched
    assert all("filament_id" not in s for s in slots)                                          # never the AMS-tray-code name


async def test_order_parts_with_a_filament_get_the_pair_and_others_are_untouched(migrated):
    parts = json.loads((await migrated.execute(text("SELECT parts FROM orders"))).scalar_one())
    assert parts[0] == {**FIXTURE_FACTS["order_parts"][0], "material_provider": "spoolman", "material_ref": "1"}
    assert parts[1] == FIXTURE_FACTS["order_parts"][1]


async def test_running_twice_is_idempotent_and_keeps_later_edits(migrated):
    await migrated.execute(text("UPDATE job_printer_configs SET material_provider='local', material_ref='m-1', filament_id=NULL"))
    await v036_inventory_refs.up(migrated)
    row = (await migrated.execute(text("SELECT filament_id, material_provider, material_ref FROM job_printer_configs"))).one()
    assert tuple(row) == (None, "local", "m-1")                                          # not overwritten from a NULL filament_id


async def test_down_removes_only_what_up_added(migrated):
    await v036_inventory_refs.down(migrated)
    for table in TABLES:
        cols = await _cols(migrated, table)
        assert "material_ref" not in cols and "material_provider" not in cols and "filament_id" in cols, table
    slots = json.loads((await migrated.execute(text("SELECT loaded_filaments FROM printers"))).scalar_one())
    assert all("inventory" not in s for s in slots) and [s["spoolman_spool_id"] for s in slots] == FIXTURE_FACTS["slot_spool_ids"]
    parts = json.loads((await migrated.execute(text("SELECT parts FROM orders"))).scalar_one())
    assert parts == FIXTURE_FACTS["order_parts"]
    await v036_inventory_refs.up(migrated)                                                  # and it re-applies
    assert "material_ref" in await _cols(migrated, "project_items")


async def test_a_fresh_install_and_malformed_json_are_fine(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    async with engine.connect() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await run_migrations(conn)
        await conn.execute(text("INSERT INTO printers (name, printer_type, connection_config, loaded_filaments, awaiting_plate_clear, "
                                "orca_printer_profiles, enabled, queue_on, no_snapshots_while_idle, bed_x_mm, bed_y_mm, lifetime_job_count, "
                                "lifetime_print_seconds) VALUES ('p','bambu','{}','null',0,'[]',1,1,0,256,256,0,0)"))
        await conn.execute(text("INSERT INTO orders (order_type, customer, title, on_hold, parts, created_at, updated_at) "
                                "VALUES ('internal','c','t',0,'not json','2026-01-01','2026-01-01')"))
        await v036_inventory_refs.up(conn)                                                  # must not raise on null / junk JSON
    await engine.dispose()
