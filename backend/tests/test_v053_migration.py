"""v053 (BIZ-314): the filament tuple. Backfill job asks / targets / project items / filament_map entries / order parts / slots from the
legacy type+colour strings (spec migration rules), return filament-blocked jobs to queued, and a working down()."""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v053_filament_tuple as mig
from app.migrations.runner import _CREATE_TABLE, _MIGRATIONS
from tests.v032_fixture import build_v032_fixture_db


async def _migrate_to(conn, max_version: int) -> None:
    await conn.execute(text(_CREATE_TABLE))
    applied = {r[0] for r in (await conn.execute(text("SELECT version FROM schema_migrations"))).fetchall()}
    for m in _MIGRATIONS:
        if m.version <= max_version and m.version not in applied:
            await m.up(conn)
            await conn.execute(text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"), {"v": m.version, "n": m.name})


@pytest.fixture
async def db(tmp_path):
    path = await build_v032_fixture_db(tmp_path / "v032.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await _migrate_to(conn, 52)
    yield engine
    await engine.dispose()


async def _rows(conn, sql, **p):
    return [dict(r) for r in (await conn.execute(text(sql), p)).mappings().all()]


async def _asks(conn, table):
    return await _rows(conn, f"SELECT filament_type, filament_color, ask_manufacturer, ask_type, ask_subtype, ask_color FROM {table}")


async def test_legacy_type_and_colour_become_a_tuple_with_subtype_any(db):
    async with db.begin() as conn:
        await mig.up(conn)
    async with db.connect() as conn:
        (cfg,) = await _asks(conn, "job_printer_configs")           # fixture: PLA / FFFFFF
        (item,) = await _asks(conn, "project_items")
    for row in (cfg, item):
        assert (row["ask_manufacturer"], row["ask_type"], row["ask_subtype"], row["ask_color"]) == (None, "PLA", "any", "FFFFFF")
        assert row["filament_type"] == "PLA"                         # the legacy columns stay as history


async def test_blank_and_any_are_undefined_not_the_string_any(db):
    async with db.begin() as conn:
        await conn.execute(text("UPDATE job_printer_configs SET filament_type = 'any', filament_color = 'ANY'"))
        await conn.execute(text("UPDATE project_items SET filament_type = '', filament_color = 'any'"))
        await mig.up(conn)
    async with db.connect() as conn:
        for table in ("job_printer_configs", "project_items"):
            (row,) = await _asks(conn, table)
            assert (row["ask_manufacturer"], row["ask_type"], row["ask_subtype"], row["ask_color"]) == (None, None, None, None)


async def test_a_legacy_plus_type_becomes_the_base_type_with_subtype_any(db):
    async with db.begin() as conn:
        await conn.execute(text("UPDATE job_printer_configs SET filament_type = 'PLA+', filament_color = 'any'"))
        await mig.up(conn)
    async with db.connect() as conn:
        (row,) = await _asks(conn, "job_printer_configs")
    assert (row["ask_type"], row["ask_subtype"], row["ask_color"]) == ("PLA", "any", None)


async def test_catalog_filament_map_entries_get_the_tuple_and_slot_pinned_entries_are_untouched(db):
    fmap = [{"model_filament": 1, "tool_index": None, "filament_id": 4, "filament_type": "PETG", "filament_color": "any"},
            {"model_filament": 2, "tool_index": 3}]
    async with db.begin() as conn:
        await conn.execute(text("UPDATE job_printer_configs SET filament_map = :m"), {"m": json.dumps(fmap)})
        await mig.up(conn)
    async with db.connect() as conn:
        (row,) = await _rows(conn, "SELECT filament_map FROM job_printer_configs")
    entries = json.loads(row["filament_map"])
    assert entries[0] == {"model_filament": 1, "tool_index": None, "filament_type": "PETG", "filament_color": None,
                          "filament_manufacturer": None, "filament_subtype": "any"}
    assert entries[1] == {"model_filament": 2, "tool_index": 3}


async def test_slots_get_a_subtype_from_the_suffix_rules_and_empty_placeholders_are_left_alone(db):
    slots = [{"slot": 0, "type": "PLA+", "color": "#FFFFFF"}, {"slot": 1, "type": "PETG-CF", "color": "#000000"},
             {"slot": 2, "type": "PLA", "color": "#FF0000"}, {"slot": 3, "type": "", "empty": True}]
    async with db.begin() as conn:
        await conn.execute(text("UPDATE printers SET loaded_filaments = :s"), {"s": json.dumps(slots)})
        await mig.up(conn)
        await mig.up(conn)                                           # idempotent
    async with db.connect() as conn:
        (row,) = await _rows(conn, "SELECT loaded_filaments FROM printers")
    got = json.loads(row["loaded_filaments"])
    assert [(s["type"], s.get("subtype"), s.get("manufacturer", "-")) for s in got] == [
        ("PLA", "plus", None), ("PETG", "cf", None), ("PLA", "standard", None), ("", None, "-")]


async def test_only_jobs_blocked_for_a_filament_mismatch_return_to_queued(db):
    reasons = {1: "loaded filament doesn't match required PLA #FF0000", 2: "tool T2 has no loaded filament",
               3: "required filament 'PETG' not loaded", 4: "a mapped tool has no loaded filament", 5: "slicing failed",
               6: "Laminus is down"}
    async with db.begin() as conn:
        await conn.execute(text("DELETE FROM jobs WHERE id != 1"))
        for jid, reason in reasons.items():
            await conn.execute(text("INSERT OR REPLACE INTO jobs (id, uploaded_file_id, plate_number, status, block_reason, queue_position,"
                                    " created_at, updated_at) VALUES (:i, 1, 1, 'blocked', :r, :i, 'x', 'x')"), {"i": jid, "r": reason})
        await mig.up(conn)
    async with db.connect() as conn:
        got = {r["id"]: (r["status"], r["block_reason"]) for r in await _rows(conn, "SELECT id, status, block_reason FROM jobs")}
    assert [got[i][0] for i in (1, 2, 3, 4)] == ["queued"] * 4 and all(got[i][1] is None for i in (1, 2, 3, 4))
    assert got[5] == ("blocked", "slicing failed") and got[6] == ("blocked", "Laminus is down")


async def test_down_drops_the_ask_columns_and_up_is_rerunnable(db):
    async with db.begin() as conn:
        await mig.up(conn)
        await mig.up(conn)
        await mig.down(conn)
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(job_printer_configs)"))).fetchall()}
    assert not {c for c in cols if c.startswith("ask_")}
