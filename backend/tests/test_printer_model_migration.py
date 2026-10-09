"""v042 backfills printer identity (plugin_id / manufacturer_id / model_id) from the legacy printer_type (BIZ-262).

The DB is the v032 fixture migrated to v041, legacy printer rows are seeded there, then v042 runs."""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations.runner import _CREATE_TABLE, _MIGRATIONS
from tests.v032_fixture import build_v032_fixture_db

LEGACY = {
    101: ("bambu", ("bambu", "bambu", "p1s")),
    102: ("elegoo_centauri", ("elegoo_centauri", "elegoo", "centauri")),
    103: ("snapmaker_extended", ("snapmaker", "snapmaker", "u1_extended")),
    104: ("mock", ("mock", "mock", "mock")),
}
IDENTITY_COLS = {"plugin_id", "manufacturer_id", "model_id"}


async def _migrate_to(conn, max_version: int) -> None:
    """Apply registered migrations up to and including `max_version` (the runner always runs to the end)."""
    await conn.execute(text(_CREATE_TABLE))
    applied = {r[0] for r in (await conn.execute(text("SELECT version FROM schema_migrations"))).fetchall()}
    for m in _MIGRATIONS:
        if m.version <= max_version and m.version not in applied:
            await m.up(conn)
            await conn.execute(text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"),
                               {"v": m.version, "n": m.name})


def _v042():
    mod = next((m for m in _MIGRATIONS if m.version == 42), None)
    assert mod is not None, "v042 printer identity migration is not registered"
    return mod


async def _printers(conn) -> list[dict]:
    rows = (await conn.execute(text("SELECT * FROM printers WHERE id >= 101 ORDER BY id"))).mappings().all()
    return [dict(r) for r in rows]


@pytest.fixture
async def v041_db(tmp_path):
    path = await build_v032_fixture_db(tmp_path / "v032.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await _migrate_to(conn, 41)
        for pid, (ptype, _) in LEGACY.items():
            await conn.execute(text(
                "INSERT INTO printers (id, name, printer_type, connection_config, loaded_filaments, orca_printer_profiles,"
                " awaiting_plate_clear, enabled, queue_on, no_snapshots_while_idle, bed_x_mm, bed_y_mm,"
                " lifetime_job_count, lifetime_print_seconds)"
                " VALUES (:id, :name, :pt, :cfg, '[]', '[]', 0, 1, 1, 0, 300, 300, 7, 900)"),
                {"id": pid, "name": f"Legacy {ptype}", "pt": ptype,
                 "cfg": json.dumps({"ip_address": f"10.0.0.{pid}", "serial_number": f"SN{pid}"})})
    yield engine
    await engine.dispose()


async def test_v042_backfills_identity_for_all_four_legacy_printer_types(v041_db):
    async with v041_db.begin() as conn:
        await _v042().up(conn)
        rows = await _printers(conn)
    got = {r["id"]: (r["plugin_id"], r["manufacturer_id"], r["model_id"]) for r in rows}
    assert got == {pid: expected for pid, (_, expected) in LEGACY.items()}


async def test_v042_keeps_connection_config_name_and_every_other_column(v041_db):
    async with v041_db.begin() as conn:
        before = await _printers(conn)
        await _v042().up(conn)
        after = await _printers(conn)
    assert len(after) == len(before) == len(LEGACY)
    for old, new in zip(before, after):
        assert {k: v for k, v in new.items() if k not in IDENTITY_COLS} == old
        assert json.loads(new["connection_config"])["serial_number"] == f"SN{old['id']}"
        assert new["printer_type"] == old["printer_type"]


async def test_v042_is_idempotent_on_rerun(v041_db):
    async with v041_db.begin() as conn:
        await _v042().up(conn)
        first = await _printers(conn)
        await _v042().up(conn)                               # second run must neither fail nor change rows
        second = await _printers(conn)
    assert second == first
    assert {(r["plugin_id"], r["manufacturer_id"], r["model_id"]) for r in second} == \
        {expected for _, expected in LEGACY.values()}
