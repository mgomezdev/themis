"""The v032 fixture DB (BIZ-204) builds deterministically and carries every Spoolman-linked datum the plugin
migration (phase 1c) must convert. Later phases open the same DB and compare against `FIXTURE_FACTS` / the golden dump."""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.golden import assert_golden
from tests.v032_fixture import FIXTURE_FACTS, build_v032_fixture_db, dump_tables


@pytest.fixture
async def v032_conn(tmp_path):
    path = await build_v032_fixture_db(tmp_path / "v032.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.connect() as conn:
        yield conn
    await engine.dispose()


async def _scalars(conn, sql):
    return [r[0] for r in (await conn.execute(text(sql))).fetchall()]


async def test_is_a_true_v032_database(v032_conn):
    assert max(await _scalars(v032_conn, "SELECT version FROM schema_migrations")) == 32
    tables = await _scalars(v032_conn, "SELECT name FROM sqlite_master WHERE type='table'")
    assert "sliced_versions" not in tables                                   # v033 is rolled back
    assert "save_slice" not in await _scalars(v032_conn, "SELECT name FROM pragma_table_info('jobs')")


async def test_spoolman_config_and_low_stock_state_are_seeded(v032_conn):
    row = (await v032_conn.execute(text("SELECT * FROM spoolman_config"))).mappings().one()
    want = FIXTURE_FACTS["spoolman"]
    assert (row["enabled"], row["url"], row["api_key"], row["sync_interval_minutes"]) == (
        1, want["url"], want["api_key"], want["sync_interval_minutes"])
    assert row["low_stock_default_g"] == want["low_stock_default_g"]
    assert json.loads(row["low_stock_overrides"]) == want["low_stock_overrides"]
    assert json.loads(row["low_stock_alerted"]) == want["low_stock_alerted"]


async def test_slots_and_every_filament_id_carrier_are_seeded(v032_conn):
    slots = json.loads((await v032_conn.execute(text("SELECT loaded_filaments FROM printers"))).scalar_one())
    assert [s["spoolman_spool_id"] for s in slots] == FIXTURE_FACTS["slot_spool_ids"]
    for table, filament_id in FIXTURE_FACTS["filament_id_by_table"].items():
        assert await _scalars(v032_conn, f"SELECT filament_id FROM {table}") == [filament_id], table
    parts = json.loads((await v032_conn.execute(text("SELECT parts FROM orders"))).scalar_one())
    assert parts == FIXTURE_FACTS["order_parts"]


async def test_api_keys_carry_scope_snapshots_including_admin_session_and_device_key(v032_conn):
    rows = (await v032_conn.execute(text("SELECT name, scopes, admin_session FROM api_keys"))).fetchall()
    assert {n: json.loads(s) for n, s, _ in rows} == FIXTURE_FACTS["api_keys"]
    assert {n for n, _, adm in rows if adm} == {"Admin session"}


async def test_full_table_dump_matches_golden(v032_conn):
    assert_golden("v032_fixture_dump", await dump_tables(v032_conn))
