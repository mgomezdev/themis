"""v052 (BIZ-294): machine profiles move from the printer to its model. Backfill from the printers' distinct (model, profile) pairs,
re-key model targets by model, and a working down()."""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v052_model_slicer_profiles as mig
from app.migrations.runner import _CREATE_TABLE, _MIGRATIONS, rollback_last, run_migrations
from tests.v032_fixture import build_v032_fixture_db

M_P1S, M_X1C = "model-p1s", "model-x1c"
P04, P06, HF = "Bambu Lab P1S 0.4 nozzle", "Bambu Lab P1S 0.6 nozzle", "Bambu Lab P1S 0.4 high flow nozzle"


async def _migrate_to(conn, max_version: int) -> None:
    await conn.execute(text(_CREATE_TABLE))
    applied = {r[0] for r in (await conn.execute(text("SELECT version FROM schema_migrations"))).fetchall()}
    for m in _MIGRATIONS:
        if m.version <= max_version and m.version not in applied:
            await m.up(conn)
            await conn.execute(text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"), {"v": m.version, "n": m.name})


async def _printer(conn, pid, model, profile, preferred=None):
    await conn.execute(text(
        "INSERT INTO printers (id, name, printer_type, connection_config, loaded_filaments, orca_printer_profiles, awaiting_plate_clear,"
        " enabled, queue_on, no_snapshots_while_idle, bed_x_mm, bed_y_mm, lifetime_job_count, lifetime_print_seconds, model_uuid,"
        " current_orca_printer_profile, preferred_slicer_plugin_id)"
        " VALUES (:id, :n, 'bambu', '{}', '[]', '[]', 0, 1, 1, 0, 256, 256, 0, 0, :m, :p, :pre)"),
        {"id": pid, "n": f"P{pid}", "m": model, "p": profile, "pre": preferred})


@pytest.fixture
async def v051_db(tmp_path):
    """The v032 fixture migrated to v051 (so printers still have current_orca_printer_profile), with two registry models."""
    path = await build_v032_fixture_db(tmp_path / "v032.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await _migrate_to(conn, 51)
        await conn.execute(text("DELETE FROM job_model_targets"))
        await conn.execute(text("DELETE FROM job_printer_configs"))
        await conn.execute(text("DELETE FROM printers"))
        for uid in (M_P1S, M_X1C):
            await conn.execute(text("INSERT INTO printer_models (id, plugin_id, manufacturer_id, model_id, manufacturer_name, display_name,"
                                    " bed_x_mm, bed_y_mm, toolheads, enabled, declared) VALUES (:i, 'bambu', 'bambu', :i, 'Bambu', :i,"
                                    " 256, 256, 1, 1, 1)"), {"i": uid})
    yield engine
    await engine.dispose()


async def _rows(conn, sql, **p):
    return [dict(r) for r in (await conn.execute(text(sql), p)).mappings().all()]


async def test_the_most_common_profile_of_a_model_becomes_its_default_and_the_printers_lose_their_per_printer_profile(v051_db):
    async with v051_db.begin() as conn:
        for pid in (1, 2, 3):
            await _printer(conn, pid, M_P1S, P04)
        await mig.up(conn)

    async with v051_db.connect() as conn:
        maps = await _rows(conn, "SELECT model_uuid, provider_plugin_id, nozzle_diameter_mm, flow, machine_profile, is_default"
                                 " FROM printer_model_slicer_profiles")
        printers = await _rows(conn, "SELECT id, machine_profile_override, nozzle_variant FROM printers ORDER BY id")
    assert maps == [{"model_uuid": M_P1S, "provider_plugin_id": "laminus", "nozzle_diameter_mm": 0.4, "flow": "standard",
                     "machine_profile": P04, "is_default": 1}]
    assert printers == [{"id": i, "machine_profile_override": None, "nozzle_variant": None} for i in (1, 2, 3)]


async def test_a_printer_on_another_nozzle_gets_a_variant_and_an_unparseable_one_keeps_a_raw_override(v051_db):
    async with v051_db.begin() as conn:
        await _printer(conn, 1, M_P1S, P04)
        await _printer(conn, 2, M_P1S, P04)
        await _printer(conn, 3, M_P1S, P06)
        await _printer(conn, 4, M_P1S, HF)
        await _printer(conn, 5, M_P1S, "My hand-made preset")
        await mig.up(conn)

    async with v051_db.connect() as conn:
        maps = {(r["nozzle_diameter_mm"], r["flow"]): r for r in await _rows(conn, "SELECT * FROM printer_model_slicer_profiles")}
        printers = {r["id"]: r for r in await _rows(conn, "SELECT id, machine_profile_override, nozzle_variant FROM printers")}
    assert set(maps) == {(0.4, "standard"), (0.6, "standard"), (0.4, "high_flow")}
    assert [m["is_default"] for m in maps.values()].count(1) == 1 and maps[(0.4, "standard")]["is_default"] == 1
    assert maps[(0.6, "standard")]["machine_profile"] == P06 and maps[(0.4, "high_flow")]["machine_profile"] == HF
    assert json.loads(printers[3]["nozzle_variant"]) == {"diameter_mm": 0.6, "flow": "standard"}
    assert json.loads(printers[4]["nozzle_variant"]) == {"diameter_mm": 0.4, "flow": "high_flow"}
    assert printers[3]["machine_profile_override"] is None and printers[4]["machine_profile_override"] is None
    assert printers[5]["machine_profile_override"] == "My hand-made preset" and printers[5]["nozzle_variant"] is None
    assert printers[1]["machine_profile_override"] is None


async def test_printers_without_a_model_keep_the_raw_override_and_models_are_mapped_independently(v051_db):
    async with v051_db.begin() as conn:
        await _printer(conn, 1, None, "Orphan preset")
        await _printer(conn, 2, M_X1C, "Bambu Lab X1 Carbon 0.4 nozzle")
        await _printer(conn, 3, M_P1S, None)                       # a printer with no profile contributes nothing
        await mig.up(conn)

    async with v051_db.connect() as conn:
        maps = await _rows(conn, "SELECT model_uuid, machine_profile FROM printer_model_slicer_profiles")
        printers = {r["id"]: r["machine_profile_override"] for r in await _rows(conn, "SELECT id, machine_profile_override FROM printers")}
    assert maps == [{"model_uuid": M_X1C, "machine_profile": "Bambu Lab X1 Carbon 0.4 nozzle"}]
    assert printers == {1: "Orphan preset", 2: None, 3: None}


async def test_a_preferred_slicer_gets_its_own_mapping_rows(v051_db):
    async with v051_db.begin() as conn:
        await _printer(conn, 1, M_P1S, P04)
        await _printer(conn, 2, M_P1S, "Other slicer's P1S", preferred="other_slicer")
        await mig.up(conn)
    async with v051_db.connect() as conn:
        maps = {r["provider_plugin_id"]: r for r in await _rows(conn, "SELECT * FROM printer_model_slicer_profiles")}
    assert maps["laminus"]["machine_profile"] == P04 and maps["other_slicer"]["machine_profile"] == "Other slicer's P1S"
    assert all(m["is_default"] == 1 for m in maps.values())


async def test_model_targets_are_rekeyed_to_the_model_of_the_printers_that_were_on_their_profile(v051_db):
    async with v051_db.begin() as conn:
        await _printer(conn, 1, M_P1S, P04)
        for tid, prof in ((7, P04), (8, "A profile no printer has")):
            await conn.execute(text("INSERT INTO job_model_targets (id, job_id, machine_profile, print_profile, filament_type,"
                                    " filament_color) VALUES (:i, 1, :p, '0.2', 'PLA', 'any')"), {"i": tid, "p": prof})
        await mig.up(conn)

    async with v051_db.connect() as conn:
        targets = await _rows(conn, "SELECT id, model_uuid, print_profile FROM job_model_targets")
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(job_model_targets)"))).fetchall()}
    assert targets == [{"id": 7, "model_uuid": M_P1S, "print_profile": "0.2"}]      # the unmatched target could never match: dropped
    assert "machine_profile" not in cols and "model_uuid" in cols


async def test_up_twice_is_a_noop(v051_db):
    async with v051_db.begin() as conn:
        await _printer(conn, 1, M_P1S, P04)
        await _printer(conn, 2, M_P1S, P06)
        await mig.up(conn)
        before = await _rows(conn, "SELECT * FROM printer_model_slicer_profiles"), await _rows(conn, "SELECT * FROM printers")
        await mig.up(conn)
        assert (await _rows(conn, "SELECT * FROM printer_model_slicer_profiles"), await _rows(conn, "SELECT * FROM printers")) == before


async def test_down_restores_each_printers_profile_and_the_targets_machine_profile(v051_db):
    async with v051_db.begin() as conn:
        await _printer(conn, 1, M_P1S, P04)
        await _printer(conn, 2, M_P1S, P04)
        await _printer(conn, 3, M_P1S, P06)
        await _printer(conn, 4, None, "Orphan preset")
        await conn.execute(text("INSERT INTO job_model_targets (id, job_id, machine_profile, print_profile, filament_type, filament_color)"
                                " VALUES (7, 1, :p, '0.2', 'PLA', 'any')"), {"p": P04})
        await mig.up(conn)
        await mig.down(conn)

    async with v051_db.connect() as conn:
        printers = {r["id"]: r["current_orca_printer_profile"] for r in await _rows(conn, "SELECT id, current_orca_printer_profile FROM printers")}
        targets = await _rows(conn, "SELECT id, machine_profile FROM job_model_targets")
        tables = {r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()}
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
    assert printers == {1: P04, 2: P04, 3: P06, 4: "Orphan preset"}
    assert targets == [{"id": 7, "machine_profile": P04}]
    assert "printer_model_slicer_profiles" not in tables and "nozzle_variant" not in cols and "machine_profile_override" not in cols


async def test_the_runner_applies_and_rolls_back_v052(v051_db):
    async with v051_db.begin() as conn:
        await _printer(conn, 1, M_P1S, P04)
        await run_migrations(conn)
        assert (await _rows(conn, "SELECT machine_profile FROM printer_model_slicer_profiles")) == [{"machine_profile": P04}]
        assert (await _rows(conn, "SELECT max(version) AS v FROM schema_migrations")) == [{"v": _MIGRATIONS[-1].version}]
        await rollback_last(conn)                                  # v053 (filament tuple) first
        await rollback_last(conn)                                  # then v052 itself
        assert (await _rows(conn, "SELECT current_orca_printer_profile AS p FROM printers")) == [{"p": P04}]
        assert (await _rows(conn, "SELECT max(version) AS v FROM schema_migrations")) == [{"v": 51}]


def test_parse_variant_reads_nozzle_and_flow_from_a_profile_name():
    assert mig.parse_variant("Bambu Lab P1S 0.4 nozzle") == (0.4, "standard")
    assert mig.parse_variant("Bambu Lab P1S 0.6 nozzle") == (0.6, "standard")
    assert mig.parse_variant("Foo 0.4 High Flow nozzle") == (0.4, "high_flow")
    assert mig.parse_variant("Foo custom") is None
