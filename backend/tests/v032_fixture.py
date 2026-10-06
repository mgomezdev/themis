"""A deterministic v032 database for migration tests (BIZ-204).

`await build_v032_fixture_db(path)` returns a SQLite file at schema v032 (v033+ not applied) seeded with everything the
Spoolman -> plugin migration must carry over: a configured Spoolman, slots with `spoolman_spool_id`, `filament_id`
on every table that has one (including `orders.parts[]` JSON), the low-stock default/overrides/alerted set, and
API keys holding `spoolman:*` / `settings:write` (staff key, admin session, browser device key — the last two are
scope *snapshots*). `FIXTURE_FACTS` states what was seeded so tests assert against it, not magic numbers."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v033_slice_cache, v036_inventory_refs, v037_inventory_deduction
from app.migrations.runner import _CREATE_TABLE, _MIGRATIONS

# Tables v001's create_all builds from today's models that v032 did not have (later phases add theirs here).
_POST_V032_TABLES = ("plugin_configs", "extension_slots", "plugin_schema_versions", "inventory_config",
                     "job_spool_snapshots", "inventory_pending_writes", "inventory_spool_status", "inventory_cache")
NOW = "2026-01-01T00:00:00"
V032_SCOPES = ["jobs:read", "jobs:write", "settings:read", "settings:write", "spoolman:read", "spoolman:write"]
_SLOTS = [
    {"slot": 0, "tool_index": 0, "type": "PLA", "color": "FFFFFF", "spoolman_spool_id": 1},
    {"slot": 1, "tool_index": 1, "type": "PETG", "color": "000000", "spoolman_spool_id": 2},
    {"slot": 2, "tool_index": 2, "type": "PLA", "color": "FF0000", "spoolman_spool_id": None},
]
FIXTURE_FACTS = {
    "spoolman": {"enabled": True, "url": "http://spoolman.fixture:7912", "api_key": "fixture-key",
                 "sync_interval_minutes": 10, "low_stock_default_g": 150.0,
                 "low_stock_overrides": {"1": 50.0, "2": 0.0}, "low_stock_alerted": [2, 7]},
    "slot_spool_ids": [1, 2, None],
    "filament_id_by_table": {"job_printer_configs": 1, "job_model_targets": 2, "project_items": 1},
    "order_parts": [{"name": "M3 magnet", "quantity": 4, "filament_id": 1}, {"name": "Brass insert", "quantity": 8}],
    "api_keys": {"Staff integration": V032_SCOPES, "Admin session": V032_SCOPES, "Browser device": V032_SCOPES,
                 "Read-only key": ["jobs:read"]},
}


async def _seed(conn) -> None:
    async def run(sql: str, **params) -> None:
        await conn.execute(text(sql), params)

    sp = FIXTURE_FACTS["spoolman"]
    await run("INSERT INTO spoolman_config (id, enabled, url, api_key, sync_interval_minutes, last_sync_at, last_attempt_at,"
              " low_stock_default_g, low_stock_overrides, low_stock_alerted) VALUES (1, 1, :url, :key, :iv, :t, :t, :d, :o, :a)",
              url=sp["url"], key=sp["api_key"], iv=sp["sync_interval_minutes"], t=NOW, d=sp["low_stock_default_g"],
              o=json.dumps(sp["low_stock_overrides"]), a=json.dumps(sp["low_stock_alerted"]))
    await run("INSERT INTO printers (id, name, printer_type, connection_config, loaded_filaments, current_orca_printer_profile,"
              " awaiting_plate_clear, orca_printer_profiles, enabled, queue_on, no_snapshots_while_idle, bed_x_mm, bed_y_mm,"
              " lifetime_job_count, lifetime_print_seconds)"
              " VALUES (1, 'Fixture P1', 'bambu', '{}', :slots, 'Bambu Lab X1C 0.4 nozzle',"
              " 0, '[]', 1, 1, 0, 256, 256, 0, 0)", slots=json.dumps(_SLOTS))
    await run("INSERT INTO uploaded_files (id, original_filename, stored_path, plates, uploaded_at,"
              " relative_path, folder, size_bytes, content_hash, mtime, missing)"
              " VALUES (1, 'benchy.3mf', '', :plates, :t, 'benchy.3mf', '/', 0, '', 0, 0)", plates=json.dumps([{"plate_number": 1, "filament_g": 12.5}]), t=NOW)
    await run("INSERT INTO orders (id, order_type, customer, title, on_hold, parts, created_at, updated_at)"
              " VALUES (1, 'internal', 'Fixture shop', 'Fixture order', 0, :parts, :t, :t)",
              parts=json.dumps(FIXTURE_FACTS["order_parts"]), t=NOW)
    await run("INSERT INTO projects (id, name, customer, order_type, on_hold, created_at, updated_at)"
              " VALUES (1, 'Fixture project', '', 'internal', 0, :t, :t)", t=NOW)
    await run("INSERT INTO project_items (project_id, file_id, quantity, quantity_completed, quantity_failed, filament_type,"
              " filament_color, filament_id, color_hex, sort_order) VALUES (1, 1, 2, 0, 0, 'PLA', 'FFFFFF', :f, '#FFFFFF', 0)", f=FIXTURE_FACTS["filament_id_by_table"]["project_items"])
    await run("INSERT INTO jobs (id, uploaded_file_id, plate_number, order_id, project_id, status, queue_position, created_at,"
              " updated_at) VALUES (1, 1, 1, 1, 1, 'queued', 1.0, :t, :t)", t=NOW)
    await run("INSERT INTO job_printer_configs (job_id, printer_id, print_profile, filament_id, filament_type, filament_color,"
              " tool_index, slice_failed) VALUES (1, 1, '0.20mm Standard', :f, 'PLA', 'FFFFFF', 0, 0)",
              f=FIXTURE_FACTS["filament_id_by_table"]["job_printer_configs"])
    await run("INSERT INTO job_model_targets (job_id, machine_profile, print_profile, filament_id, filament_type, filament_color)"
              " VALUES (1, 'Bambu Lab X1C 0.4 nozzle', '0.20mm Standard', :f, 'PETG', '000000')",
              f=FIXTURE_FACTS["filament_id_by_table"]["job_model_targets"])
    for i, (name, scopes) in enumerate(FIXTURE_FACTS["api_keys"].items(), start=1):
        await run("INSERT INTO api_keys (name, key_prefix, key_hash, scopes, enabled, created_at, admin_session)"
                  " VALUES (:n, :p, :h, :s, 1, :t, :adm)", n=name, p=f"fix{i:05d}", h=f"{i:064x}",
                  s=json.dumps(scopes), t=NOW, adm=1 if name == "Admin session" else 0)


async def _build(path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_CREATE_TABLE))
            for m in [m for m in _MIGRATIONS if m.version <= 33]:
                await m.up(conn)
                await conn.execute(text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"),
                                   {"v": m.version, "n": m.name})
            # v001 builds the *current* models (tables added by later phases too): drop what post-v032 migrations own.
            await v033_slice_cache.down(conn)
            await conn.execute(text("DELETE FROM schema_migrations WHERE version = 33"))
            await v036_inventory_refs.down(conn)                       # drops the material_* columns create_all added
            await conn.execute(text("ALTER TABLE jobs DROP COLUMN deduction_note"))   # added by create_all (v037)
            for table in _POST_V032_TABLES:
                await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
            await _seed(conn)
    finally:
        await engine.dispose()


async def build_v032_fixture_db(path: Path) -> Path:
    """Create the fixture at `path` (must not exist) and return it. Use `tmp_path / "v032.db"`."""
    path = Path(path)
    assert not path.exists(), f"{path} already exists"
    await _build(path)
    return path


async def dump_tables(conn) -> dict:
    """Every table's rows as JSON-able dicts (JSON columns left as stored text), ordered by rowid: the fixture's golden form."""
    names = [r[0] for r in (await conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"))).fetchall()]
    out: dict[str, list] = {}
    for n in names:
        rows = (await conn.execute(text(f'SELECT * FROM "{n}" ORDER BY rowid'))).mappings().all()
        if n != "schema_migrations" and rows:
            out[n] = [dict(r) for r in rows]
    return out
