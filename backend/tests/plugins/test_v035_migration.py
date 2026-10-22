"""v035 moves the Spoolman integration onto the plugin host: run on the phase-0 v032 fixture, re-read every row, run twice
(idempotent), `down()`, and the fresh-DB `create_all` path."""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base
from app.migrations import v035_inventory_core
from app.migrations.runner import run_migrations
from tests.plugins.legacy_shape import restore_pre_040_shape
from tests.v032_fixture import FIXTURE_FACTS, build_v032_fixture_db


@pytest.fixture
async def migrated(tmp_path):
    path = await build_v032_fixture_db(tmp_path / "v032.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await run_migrations(conn)                       # v033 .. latest on top of the v032 database
        await restore_pre_040_shape(conn)       # v035 is tested in the shape it ran in (before slots became selections)
    conn = await engine.connect()
    yield conn
    await conn.close()
    await engine.dispose()


async def _one(conn, sql, **p):
    return (await conn.execute(text(sql), p)).mappings().first()


async def _scopes(conn) -> dict[str, list[str]]:
    return {r[0]: json.loads(r[1]) for r in (await conn.execute(text("SELECT name, scopes FROM api_keys"))).fetchall()}


async def test_the_spoolman_config_becomes_the_spoolman_plugin_config_and_the_active_provider(migrated):
    want = FIXTURE_FACTS["spoolman"]
    row = await _one(migrated, "SELECT * FROM plugin_configs WHERE plugin_id='spoolman'")
    assert row["enabled"] == 1
    assert json.loads(row["settings"]) == {"url": want["url"], "sync_interval_minutes": want["sync_interval_minutes"]}
    assert json.loads(row["secrets"]) == {"api_key": want["api_key"]}                   # the secret is not in `settings`
    assert json.loads(row["state"]) == {"last_sync_at": "2026-01-01T00:00:00", "last_attempt_at": "2026-01-01T00:00:00"}
    assert (await _one(migrated, "SELECT plugin_id FROM extension_slots WHERE kind='filament_inventory'"))["plugin_id"] == "spoolman"


async def test_low_stock_state_is_rekeyed_with_the_provider_namespace(migrated):
    want = FIXTURE_FACTS["spoolman"]
    row = await _one(migrated, "SELECT * FROM inventory_config WHERE id=1")
    assert row["deduct_on_complete"] == 1 and row["low_stock_default_g"] == want["low_stock_default_g"]
    assert json.loads(row["low_stock_overrides"]) == {f"spoolman:{k}": v for k, v in want["low_stock_overrides"].items()}
    assert json.loads(row["low_stock_alerted"]) == [f"spoolman:{i}" for i in want["low_stock_alerted"]]


async def test_inventory_scopes_are_granted_to_spoolman_and_settings_keys_including_the_snapshots(migrated):
    scopes = await _scopes(migrated)
    for name in ("Staff integration", "Admin session", "Browser device"):              # the admin session + device key snapshots
        assert {"inventory:read", "inventory:write"} <= set(scopes[name]), name
        assert set(FIXTURE_FACTS["api_keys"][name]) <= set(scopes[name])                # nothing was taken away
    assert scopes["Read-only key"] == ["jobs:read"]                                      # unrelated keys are untouched


async def test_up_leaves_the_legacy_spoolman_config_row_untouched(migrated):
    row = await _one(migrated, "SELECT * FROM spoolman_config WHERE id=1")
    want = FIXTURE_FACTS["spoolman"]
    assert (row["url"], row["api_key"], row["low_stock_default_g"]) == (want["url"], want["api_key"], want["low_stock_default_g"])
    assert json.loads(row["low_stock_overrides"]) == want["low_stock_overrides"]            # still the plain filament ids


async def test_running_twice_is_idempotent_and_never_overwrites_later_edits(migrated):
    await migrated.execute(text("UPDATE plugin_configs SET enabled = 0 WHERE plugin_id='spoolman'"))
    await migrated.execute(text("UPDATE inventory_config SET low_stock_default_g = 999"))
    await v035_inventory_core.up(migrated)                                              # a second run (or a downgrade/upgrade)
    assert (await _one(migrated, "SELECT enabled FROM plugin_configs WHERE plugin_id='spoolman'"))["enabled"] == 0
    assert (await _one(migrated, "SELECT low_stock_default_g FROM inventory_config"))["low_stock_default_g"] == 999
    assert (await migrated.execute(text("SELECT count(*) FROM plugin_configs"))).scalar_one() == 1
    scopes = await _scopes(migrated)
    assert scopes["Staff integration"].count("inventory:read") == 1


async def test_down_removes_what_up_created_and_restores_the_scopes_but_keeps_spoolman_config(migrated):
    await v035_inventory_core.down(migrated)
    tables = {r[0] for r in (await migrated.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()}
    assert "inventory_config" not in tables
    assert (await migrated.execute(text("SELECT count(*) FROM plugin_configs"))).scalar_one() == 0
    assert (await migrated.execute(text("SELECT count(*) FROM extension_slots"))).scalar_one() == 0
    assert (await _scopes(migrated))["Staff integration"] == FIXTURE_FACTS["api_keys"]["Staff integration"]
    assert (await _one(migrated, "SELECT url FROM spoolman_config"))["url"] == FIXTURE_FACTS["spoolman"]["url"]
    assert (await _one(migrated, "SELECT low_stock_default_g FROM spoolman_config"))["low_stock_default_g"] == FIXTURE_FACTS["spoolman"]["low_stock_default_g"]
    await v035_inventory_core.up(migrated)                                                  # and it re-applies after a downgrade
    assert (await _one(migrated, "SELECT plugin_id FROM extension_slots"))["plugin_id"] == "spoolman"


async def _fresh_conn(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    conn = await engine.connect()
    await conn.run_sync(Base.metadata.create_all)
    await restore_pre_040_shape(conn)              # v035 predates capability_selections: give it the extension_slots it expects
    return engine, conn


async def test_a_fresh_install_has_the_tables_and_no_provider(tmp_path):
    engine, conn = await _fresh_conn(tmp_path)
    try:
        await run_migrations(conn)
        await run_migrations(conn)
        assert (await conn.execute(text("SELECT count(*) FROM plugin_configs"))).scalar_one() == 0
        assert (await conn.execute(text("SELECT count(*) FROM capability_selections"))).scalar_one() == 0
        assert (await conn.execute(text("SELECT count(*) FROM inventory_config"))).scalar_one() == 0     # created lazily on first use
    finally:
        await conn.close()
        await engine.dispose()


async def test_an_install_that_never_set_up_a_spoolman_url_gets_a_disabled_plugin_row_and_no_slot(tmp_path):
    engine, conn = await _fresh_conn(tmp_path)
    try:
        await conn.execute(text("INSERT INTO spoolman_config (id, enabled, sync_interval_minutes) VALUES (1, 0, 15)"))
        await conn.execute(text("INSERT INTO api_keys (name, key_prefix, key_hash, scopes, enabled, created_at) "
                                "VALUES ('reader', 'rdr00001', 'h', '[\"spoolman:read\"]', 1, '2026-01-01T00:00:00'),"
                                "('writer', 'wrt00001', 'h2', '[\"settings:write\"]', 1, '2026-01-01T00:00:00')"))
        await v035_inventory_core.up(conn)
        row = await _one(conn, "SELECT * FROM plugin_configs WHERE plugin_id='spoolman'")
        assert row["enabled"] == 0 and json.loads(row["settings"]) == {"sync_interval_minutes": 15} and json.loads(row["secrets"]) == {}
        assert (await conn.execute(text("SELECT count(*) FROM extension_slots"))).scalar_one() == 0
        scopes = await _scopes(conn)
        assert set(scopes["reader"]) == {"spoolman:read", "inventory:read"}                  # read stays read-only
        assert {"settings:write", "inventory:read", "inventory:write"} <= set(scopes["writer"])
    finally:
        await conn.close()
        await engine.dispose()


async def test_json_nulls_in_the_legacy_config_do_not_abort_the_migration(tmp_path):
    engine, conn = await _fresh_conn(tmp_path)
    try:
        await conn.execute(text("INSERT INTO spoolman_config (id, enabled, url, sync_interval_minutes, low_stock_overrides, low_stock_alerted) "
                                "VALUES (1, 1, 'http://sm.test', 15, 'null', 'null')"))
        await v035_inventory_core.up(conn)
        row = await _one(conn, "SELECT * FROM inventory_config")
        assert json.loads(row["low_stock_overrides"]) == {} and json.loads(row["low_stock_alerted"]) == []
    finally:
        await conn.close()
        await engine.dispose()


async def test_down_writes_post_upgrade_edits_back_to_the_legacy_config(migrated):
    await migrated.execute(text("UPDATE plugin_configs SET enabled = 0, settings = :s, secrets = :k WHERE plugin_id='spoolman'"),
                           {"s": json.dumps({"url": "http://edited.test", "sync_interval_minutes": 3}), "k": json.dumps({"api_key": "new-key"})})
    await migrated.execute(text("UPDATE inventory_config SET low_stock_default_g = 77, low_stock_overrides = :o, low_stock_alerted = :a"),
                           {"o": json.dumps({"spoolman:9": 12.5, "other:1": 3.0}), "a": json.dumps(["spoolman:4", "other:2"])})

    await v035_inventory_core.down(migrated)

    row = await _one(migrated, "SELECT * FROM spoolman_config WHERE id=1")
    assert (row["enabled"], row["url"], row["api_key"], row["sync_interval_minutes"]) == (0, "http://edited.test", "new-key", 3)
    assert row["low_stock_default_g"] == 77
    assert json.loads(row["low_stock_overrides"]) == {"9": 12.5}                    # de-namespaced; other providers' state is dropped
    assert json.loads(row["low_stock_alerted"]) == [4]
