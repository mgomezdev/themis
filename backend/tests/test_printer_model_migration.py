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
        # the fixture is built from the current models, so the identity columns exist before v042 too: compare the rest
        assert {k: v for k, v in new.items() if k not in IDENTITY_COLS} == {k: v for k, v in old.items() if k not in IDENTITY_COLS}
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


def _v043():
    mod = next((m for m in _MIGRATIONS if m.version == 43), None)
    assert mod is not None, "v043 printer-model registry migration is not registered"
    return mod


async def test_v043_creates_one_registry_row_per_model_key_and_points_every_printer_at_it(v041_db):
    async with v041_db.begin() as conn:
        await _v042().up(conn)
        await conn.execute(text(  # a second Bambu on the same model must share the registry row
            "INSERT INTO printers (id, name, printer_type, connection_config, loaded_filaments, orca_printer_profiles,"
            " awaiting_plate_clear, enabled, queue_on, no_snapshots_while_idle, bed_x_mm, bed_y_mm, lifetime_job_count,"
            " lifetime_print_seconds, plugin_id, manufacturer_id, model_id) VALUES (105, 'B2', 'bambu', '{}', '[]', '[]', 0, 1, 1,"
            " 0, 256, 256, 0, 0, 'bambu', 'bambu', 'p1s')"))
        await _v043().up(conn)
        models = (await conn.execute(text("SELECT id, plugin_id, manufacturer_id, model_id, enabled FROM printer_models"))).fetchall()
        printers = {r[0]: r[1] for r in (await conn.execute(text("SELECT id, model_uuid FROM printers WHERE id >= 101"))).fetchall()}
    assert len(models) == len(LEGACY) and all(m[4] == 1 for m in models)
    by_key = {(m[1], m[2], m[3]): m[0] for m in models}
    assert printers[101] == printers[105] == by_key[("bambu", "bambu", "p1s")]
    assert {printers[pid] for pid in LEGACY} == set(by_key.values())


async def test_v043_is_idempotent_and_keeps_uuids_on_rerun(v041_db):
    async with v041_db.begin() as conn:
        await _v042().up(conn)
        await _v043().up(conn)
        first = (await conn.execute(text("SELECT id, model_uuid FROM printers WHERE id >= 101 ORDER BY id"))).fetchall()
        await _v043().up(conn)
        second = (await conn.execute(text("SELECT id, model_uuid FROM printers WHERE id >= 101 ORDER BY id"))).fetchall()
        n = (await conn.execute(text("SELECT COUNT(*) FROM printer_models"))).scalar()
    assert second == first and n == len(LEGACY)


def _v044():
    mod = next((m for m in _MIGRATIONS if m.version == 44), None)
    assert mod is not None, "v044 file machine eligibility migration is not registered"
    return mod


async def _cached_slice(conn, file_id: int, preset: str) -> None:
    await conn.execute(text(
        "INSERT INTO uploaded_files (id, original_filename, stored_path, plates, uploaded_at, relative_path, folder, size_bytes,"
        " content_hash, mtime, missing) VALUES (:i, :n, '', '[]', '2026-01-01', :n, '/', 1, 'h', 0, 0)"),
        {"i": file_id, "n": f"slice{file_id}.gcode"})
    await conn.execute(text(
        "INSERT INTO sliced_versions (file_id, plate_number, machine_preset, process_preset, filament_presets, extra_config,"
        " artifact_kind, cache_key, filament_type, filament_color, created_at, source_content_hash)"
        " VALUES (:i, 1, :p, 'proc', '[]', '{}', 'gcode', :k, 'any', 'any', '2026-01-01', '')"),
        {"i": file_id, "p": preset, "k": f"k{file_id}"})


async def test_v044_backfills_a_cached_slice_only_when_its_machine_preset_maps_to_one_model(v041_db):
    async with v041_db.begin() as conn:
        await _v042().up(conn)
        await _v043().up(conn)
        models = {r[0]: r[1] for r in (await conn.execute(text("SELECT model_id, id FROM printer_models"))).fetchall()}
        await conn.execute(text("UPDATE printers SET current_orca_printer_profile = 'Bambu 0.4' WHERE id = 101"))
        await conn.execute(text("UPDATE printers SET current_orca_printer_profile = 'Mixed 0.4' WHERE id IN (102, 103)"))
        await _cached_slice(conn, 9001, "Bambu 0.4")          # one printer, one model -> backfilled
        await _cached_slice(conn, 9002, "Mixed 0.4")          # two different models on one preset -> stays unknown
        await _cached_slice(conn, 9003, "No printer has this")  # no printer -> stays unknown
        await _v044().up(conn)
        known = {r[0]: r[1] for r in (await conn.execute(text("SELECT id, eligibility_known FROM uploaded_files WHERE id >= 9000"))).fetchall()}
        rows = (await conn.execute(text("SELECT file_id, model_uuid, source FROM file_machine_eligibility"))).fetchall()
    assert known == {9001: 1, 9002: 0, 9003: 0}
    assert rows == [(9001, models["p1s"], "backfill")]


async def test_v044_is_idempotent_and_never_marks_unrelated_gcode_files_known(v041_db):
    async with v041_db.begin() as conn:
        await _v042().up(conn)
        await _v043().up(conn)
        await conn.execute(text("UPDATE printers SET current_orca_printer_profile = 'Bambu 0.4' WHERE id = 101"))
        await _cached_slice(conn, 9001, "Bambu 0.4")
        await conn.execute(text(
            "INSERT INTO uploaded_files (id, original_filename, stored_path, plates, uploaded_at, relative_path, folder, size_bytes,"
            " content_hash, mtime, missing) VALUES (9100, 'legacy.gcode', '', '[]', '2026-01-01', 'legacy.gcode', '/', 1, 'h', 0, 0)"))
        await _v044().up(conn)
        first = (await conn.execute(text("SELECT id, eligibility_known FROM uploaded_files WHERE id >= 9000 ORDER BY id"))).fetchall()
        await _v044().up(conn)
        second = (await conn.execute(text("SELECT id, eligibility_known FROM uploaded_files WHERE id >= 9000 ORDER BY id"))).fetchall()
        n = (await conn.execute(text("SELECT COUNT(*) FROM file_machine_eligibility"))).scalar()
    assert first == second == [(9001, 1), (9100, 0)]
    assert n == 1
