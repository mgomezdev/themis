"""The plugin data lifecycle (BIZ-302, docs/plugin-lifecycle.md): what disable, uninstall, reinstall, upgrade, failed upgrade and
rollback do to plugin-owned data, and what they never touch (core-owned rows and tables)."""
import types

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app import plugins
from app.models import CapabilitySelection, InstalledPlugin, JobSpoolSnapshot, PluginConfig, Printer
from app.plugins import _REGISTRY, installer, loader, migrations as plugin_migrations
from app.plugins.manifest import PluginError
from tests.plugins import pkg_builder as pb
from tests.plugins.dummy_plugin import make_manifest, migration
# the admin client + data dir fixtures the install routes are tested with
from tests.plugins.test_plugin_install_routes import admin, data_dir  # noqa: F401

ITEMS_UP = "CREATE TABLE IF NOT EXISTS lc_demo_items (id INTEGER PRIMARY KEY, name TEXT)"
ITEMS_DOWN = "DROP TABLE IF EXISTS lc_demo_items"


@pytest.fixture
async def conn(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'p.db'}")
    async with engine.begin() as c:
        yield c
    await engine.dispose()


def _manifest(*migrations, plugin_id="lc_demo"):
    return make_manifest(plugin_id, migrations=tuple(migrations))


# ---- manifest metadata: migration ownership and sequencing -----------------------------------------------------------------

@pytest.mark.parametrize("versions", [(1, 1), (0,), (-2,), (1, 3, 3)])
def test_migration_versions_must_be_unique_positive_integers(versions):
    mods = tuple(migration(v, ITEMS_UP, ITEMS_DOWN) for v in versions)
    with pytest.raises(PluginError, match="unique positive integers"):
        _manifest(*mods)


def test_gaps_in_the_version_sequence_are_fine():
    assert [m.version for m in _manifest(migration(2, ITEMS_UP, ITEMS_DOWN), migration(7, "SELECT 1", "SELECT 1")).migrations] == [2, 7]


async def test_the_detail_says_what_the_plugin_owns_and_which_host_api_it_targets(client, session_factory):
    _REGISTRY["lc_demo"] = _manifest(migration(1, ITEMS_UP, ITEMS_DOWN), migration(2, "SELECT 1", "SELECT 1"))
    async with session_factory() as s:
        await s.execute(text(ITEMS_UP))
        await s.execute(text("INSERT INTO plugin_schema_versions (plugin_id, version, name) VALUES ('lc_demo', 1, 'm')"))
        await s.commit()

    data = (await client.get("/api/v1/plugins/lc_demo")).json()["data"]

    assert data == {"host_api": 1, "supported_host_api": [1], "table_prefix": "lc_demo_", "tables": ["lc_demo_items"],
                    "migrations": {"shipped": [1, 2], "applied": [1]}}


# ---- uninstall can never delete what the plugin does not own ---------------------------------------------------------------

async def test_a_down_that_drops_a_table_outside_the_prefix_is_refused_and_rolled_back(conn):
    await conn.execute(text("CREATE TABLE core_things (id INTEGER)"))
    hostile = migration(1, ITEMS_UP, "DROP TABLE core_things; ")

    async def down(c):
        await c.execute(text("DROP TABLE lc_demo_items"))
        await c.execute(text("DROP TABLE core_things"))
    hostile.down = down
    m = _manifest(hostile)
    assert await plugin_migrations.run_plugin_migrations(conn, [m]) == {}

    with pytest.raises(PluginError, match=r"outside its prefix 'lc_demo_': \['core_things'\]"):
        await plugin_migrations.rollback_plugin_migration(conn, m)

    tables = {r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()}
    assert {"core_things", "lc_demo_items"} <= tables                                  # nothing dropped, own table included
    versions = (await conn.execute(text("SELECT version FROM plugin_schema_versions WHERE plugin_id='lc_demo'"))).fetchall()
    assert [v[0] for v in versions] == [1]                                             # still recorded as applied


async def test_uninstalling_with_data_removal_is_refused_when_down_would_drop_core_data_and_nothing_changes(admin, session_factory):
    async with session_factory() as s:
        s.add(PluginConfig(plugin_id="lc_demo", enabled=True, settings={}))
        s.add(InstalledPlugin(plugin_id="lc_demo", version="1.0.0", source="upload", archive_sha256="x", installed_at="t", status="active"))
        await s.execute(text(ITEMS_UP))
        await s.execute(text("INSERT INTO plugin_schema_versions (plugin_id, version, name) VALUES ('lc_demo', 1, 'm')"))
        await s.commit()
    hostile = migration(1, ITEMS_UP, ITEMS_DOWN)

    async def down(c):
        await c.execute(text("DROP TABLE printers"))
    hostile.down = down
    _REGISTRY["lc_demo"] = _manifest(hostile)
    from app.plugins.host import plugin_host
    await plugin_host.reload()                                                              # the host knows it is enabled

    r = await admin.delete("/api/v1/plugins/lc_demo?remove_data=true")

    assert r.status_code == 422 and "outside its prefix" in r.json()["detail"]
    async with session_factory() as s:
        assert (await s.execute(text("SELECT count(*) FROM printers"))).scalar() == 0           # the table still exists
        assert (await s.get(InstalledPlugin, "lc_demo")).status == "active"                      # not marked for removal
        assert (await s.get(PluginConfig, "lc_demo")).enabled is True                            # and not left stopped either


# ---- disable and uninstall keep data, and never invalidate core-owned records ----------------------------------------------

async def _seed_core_references(s, plugin_id: str):
    """Core-owned rows that name the plugin (a printer bound to it, a spool snapshot of its provider)."""
    s.add(Printer(name="Bound", printer_type="bambu", connection_config={}, plugin_id=plugin_id))
    await s.flush()


async def test_disabling_a_plugin_keeps_its_data_settings_and_selection_and_the_core_records_that_name_it(host, session_factory):
    _REGISTRY["lc_demo"] = _manifest(migration(1, ITEMS_UP, ITEMS_DOWN))
    async with session_factory() as s:
        await s.execute(text(ITEMS_UP))
        await s.execute(text("INSERT INTO lc_demo_items VALUES (1, 'kept')"))
        await _seed_core_references(s, "lc_demo")
        await s.commit()
    await host.update_config("lc_demo", settings={"url": "http://lc.test"}, enabled=True)
    await host.set_provider("lc_demo.ping", "lc_demo")

    await host.update_config("lc_demo", enabled=False)

    assert host.active("lc_demo.ping") is None                                          # it stopped serving...
    async with session_factory() as s:                                                   # ...but nothing was deleted
        assert (await s.execute(text("SELECT name FROM lc_demo_items"))).scalars().all() == ["kept"]
        cfg = await s.get(PluginConfig, "lc_demo")
        assert (cfg.enabled, cfg.settings["url"]) == (False, "http://lc.test")
        assert (await s.get(CapabilitySelection, "lc_demo.ping")).plugin_id == "lc_demo"
        assert (await s.execute(select(Printer.plugin_id))).scalars().all() == ["lc_demo"]
    await host.update_config("lc_demo", enabled=True)
    assert host.active("lc_demo.ping") is not None                                       # and it comes back as it was


async def test_uninstall_keeps_data_then_a_reinstall_reclaims_it_without_rerunning_migrations(admin, session_factory):
    runs = []
    first = migration(1, ITEMS_UP, ITEMS_DOWN)
    original_up = first.up

    async def up(c):
        runs.append(1)
        await original_up(c)
    first.up = up
    m = _manifest(first)
    _REGISTRY["lc_demo"] = m
    async with session_factory() as s:
        s.add(PluginConfig(plugin_id="lc_demo", enabled=True, settings={"url": "http://lc.test"}))
        s.add(InstalledPlugin(plugin_id="lc_demo", version="1.0.0", source="upload", archive_sha256="x", installed_at="t", status="active"))
        await s.commit()
        conn = await s.connection()
        await plugin_migrations.run_plugin_migrations(conn, [m])
        await s.execute(text("INSERT INTO lc_demo_items VALUES (1, 'precious')"))
        await _seed_core_references(s, "lc_demo")
        await s.commit()
    assert runs == [1]

    assert (await admin.delete("/api/v1/plugins/lc_demo")).status_code == 200            # uninstall, data kept (the default)
    del _REGISTRY["lc_demo"]                                                              # the restart removed the code
    async with session_factory() as s:
        await s.delete(await s.get(InstalledPlugin, "lc_demo"))                           # loader.reconcile drops the row
        await s.commit()
        assert (await s.execute(text("SELECT name FROM lc_demo_items"))).scalars().all() == ["precious"]
        assert (await s.get(PluginConfig, "lc_demo")).settings == {"url": "http://lc.test"}
        assert (await s.execute(select(Printer.plugin_id))).scalars().all() == ["lc_demo"]          # core record untouched, now dormant

    _REGISTRY["lc_demo"] = m                                                              # reinstall the same plugin
    async with session_factory() as s:
        errors = await plugin_migrations.run_plugin_migrations(await s.connection(), [m])
        await s.commit()
        assert errors == {} and runs == [1]                                               # recorded as applied: not re-run
        assert (await s.execute(text("SELECT name FROM lc_demo_items"))).scalars().all() == ["precious"]
        assert (await s.get(PluginConfig, "lc_demo")).enabled is False                    # back OFF until the admin enables it again


async def test_uninstall_with_data_removal_removes_only_the_plugins_own_data(admin, session_factory):
    m = _manifest(migration(1, ITEMS_UP, ITEMS_DOWN))
    _REGISTRY["lc_demo"] = m
    async with session_factory() as s:
        s.add(PluginConfig(plugin_id="lc_demo", enabled=True, settings={}))
        s.add(InstalledPlugin(plugin_id="lc_demo", version="1.0.0", source="upload", archive_sha256="x", installed_at="t", status="active"))
        await plugin_migrations.run_plugin_migrations(await s.connection(), [m])
        await _seed_core_references(s, "lc_demo")
        await s.commit()

    assert (await admin.delete("/api/v1/plugins/lc_demo?remove_data=true")).status_code == 200

    async with session_factory() as s:
        assert (await s.execute(text("SELECT 1 FROM sqlite_master WHERE name='lc_demo_items'"))).first() is None
        assert await s.get(PluginConfig, "lc_demo") is None
        assert (await s.execute(select(Printer.plugin_id))).scalars().all() == ["lc_demo"]          # the printer row survives, dormant


async def test_core_records_that_hold_opaque_provider_refs_survive_the_providers_removal_untouched(admin, session_factory, create_job):
    """Spool snapshots keep `provider` + `spool_ref` as plain strings (no FK to any plugin): uninstalling the provider and
    removing its data leaves the job history readable, exactly as it was."""
    job_id = await create_job()
    m = _manifest(migration(1, ITEMS_UP, ITEMS_DOWN), plugin_id="gone_inv")
    _REGISTRY["gone_inv"] = m
    async with session_factory() as s:
        s.add(PluginConfig(plugin_id="gone_inv", enabled=True, settings={}))
        s.add(InstalledPlugin(plugin_id="gone_inv", version="1.0.0", source="upload", archive_sha256="x", installed_at="t", status="active"))
        s.add(JobSpoolSnapshot(job_id=job_id, provider="gone_inv", spool_ref="gone_inv:9", pre_weight_g=812.5, source="live",
                               taken_at="2026-10-01T10:00:00"))
        await plugin_migrations.run_plugin_migrations(await s.connection(), [m])
        await s.commit()

    assert (await admin.delete("/api/v1/plugins/gone_inv?remove_data=true")).status_code == 200

    async with session_factory() as s:
        snap = (await s.execute(select(JobSpoolSnapshot))).scalar_one()
        assert (snap.job_id, snap.provider, snap.spool_ref, snap.pre_weight_g) == (job_id, "gone_inv", "gone_inv:9", 812.5)
        assert await s.get(PluginConfig, "gone_inv") is None                                   # the plugin's own data is gone


# ---- failed upgrade: Themis boots, the failure is reported, the previous version can be restored ---------------------------

UPGRADE_CODE = '''import types
from sqlalchemy import text
from pydantic import BaseModel
from app.plugins import PluginManifest, Provide
from app.plugins.capabilities.filament_inventory import CAPABILITY


class Settings(BaseModel):
    url: str = ""


def _mig(version, up_sql, down_sql):
    m = types.ModuleType(f"lc_up_m{{version}}")
    m.version, m.name = version, f"m{{version}}"
    async def up(conn):
        for stmt in up_sql:
            await conn.execute(text(stmt))
    async def down(conn):
        await conn.execute(text(down_sql))
    m.up, m.down = up, down
    return m


MIGRATIONS = {migrations}

MANIFEST = PluginManifest(id="lc_up", name="Upgradable", version="{version}", host_api=1, settings_model=Settings,
                          factory=lambda s: None, provides={{CAPABILITY: Provide(version=1)}}, migrations=tuple(MIGRATIONS))
'''
V1_MIGRATIONS = '[_mig(1, ["CREATE TABLE IF NOT EXISTS lc_up_items (id INTEGER PRIMARY KEY, name TEXT)"], "DROP TABLE IF EXISTS lc_up_items")]'
V2_BROKEN = ('[_mig(1, ["CREATE TABLE IF NOT EXISTS lc_up_items (id INTEGER PRIMARY KEY, name TEXT)"], "DROP TABLE IF EXISTS lc_up_items"), '
             '_mig(2, ["CREATE TABLE lc_up_extra (id INTEGER)", "SELECT * FROM does_not_exist"], "DROP TABLE IF EXISTS lc_up_extra")]')
V2_PARTIAL = ('[_mig(1, ["CREATE TABLE IF NOT EXISTS lc_up_items (id INTEGER PRIMARY KEY, name TEXT)"], "DROP TABLE IF EXISTS lc_up_items"), '
              '_mig(2, ["CREATE TABLE lc_up_extra (id INTEGER)"], "DROP TABLE IF EXISTS lc_up_extra"), '
              '_mig(3, ["SELECT * FROM does_not_exist"], "SELECT 1")]')


def _pkg(version: str, migrations: str) -> bytes:
    toml = pb.TOML.format(id="lc_up", name="Upgradable", version=version, provides='["inventory.filament@1"]', host_api=1, pkg="lc_up")
    return pb.make_zip(pb.files("lc_up", version, toml=toml, code=UPGRADE_CODE.format(version=version, migrations=migrations)))


@pytest.fixture
def clean_registry():
    import sys
    saved_reg, saved_path, saved_mods = dict(_REGISTRY), list(sys.path), set(sys.modules)
    plugin_migrations.failed.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved_reg)
    sys.path[:] = saved_path
    for name in set(sys.modules) - saved_mods:
        del sys.modules[name]
    plugin_migrations.failed.clear()


async def _install(session_factory, version, migrations):
    staged = await installer.stage_upload(_chunks(_pkg(version, migrations)), "p.zip")
    async with session_factory() as s:
        return await installer.commit(s, staged.token, actor="a")


async def _chunks(b: bytes):
    yield b


async def _restart(session_factory, db_path):
    """What the app does on startup, in order: load installed code, run plugin migrations, reconcile the rows."""
    for pid in [p for p in _REGISTRY if p == "lc_up"]:
        del _REGISTRY[pid]
    import sys
    for name in [n for n in sys.modules if n == "lc_up" or n.startswith("lc_up.")]:
        del sys.modules[name]
    sys.path[:] = [p for p in sys.path if "/lc_up/" not in p.replace("\\", "/")]            # a restart starts with a clean path
    report = loader.load_installed(installer.plugins_dir(), db_path)
    async with session_factory() as s:
        errors = await plugin_migrations.run_plugin_migrations(await s.connection())
        await s.commit()
    await loader.reconcile(session_factory, report, errors)
    return report, errors


async def _row(session_factory):
    async with session_factory() as s:
        r = await s.get(InstalledPlugin, "lc_up")
        return r.status, r.version, r.previous_version, r.error


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


async def test_a_failed_upgrade_boots_reports_a_recoverable_state_and_rolls_back_to_the_working_version(
        data_dir, session_factory, db_path, clean_registry):  # noqa: F811
    await _install(session_factory, "1.0.0", V1_MIGRATIONS)
    report, errors = await _restart(session_factory, db_path)
    assert errors == {} and report.errors == {} and await _row(session_factory) == ("active", "1.0.0", None, None)
    async with session_factory() as s:
        await s.execute(text("INSERT INTO lc_up_items VALUES (1, 'precious')"))
        await s.commit()

    await _install(session_factory, "1.1.0", V2_BROKEN)                                   # upgrade; applies at the restart
    report, errors = await _restart(session_factory, db_path)                             # Themis still boots (no exception)

    assert "migration v2 (m2) failed" in errors["lc_up"] and "does_not_exist" in errors["lc_up"]
    status, version, previous, error = await _row(session_factory)
    assert (status, version, previous) == ("error", "1.1.0", "1.0.0") and "migration v2" in error      # reported, not hidden
    assert plugin_migrations.failed["lc_up"] == errors["lc_up"]                                          # the host will not build it
    async with session_factory() as s:
        assert (await s.execute(text("SELECT name FROM lc_up_items"))).scalars().all() == ["precious"]    # v1's data is intact
        assert (await s.execute(text("SELECT 1 FROM sqlite_master WHERE name='lc_up_extra'"))).first() is None   # the failed step left no trace
        assert (await s.execute(text("SELECT version FROM plugin_schema_versions WHERE plugin_id='lc_up'"))).scalars().all() == [1]

    async with session_factory() as s:                                                     # recoverable: roll back, restart
        assert (await installer.rollback(s, "lc_up", actor="a")).version == "1.0.0"
    report, errors = await _restart(session_factory, db_path)
    assert errors == {} and report.loaded == {"lc_up": "1.0.0"}
    assert await _row(session_factory) == ("active", "1.0.0", "1.1.0", None)
    async with session_factory() as s:
        assert (await s.execute(text("SELECT name FROM lc_up_items"))).scalars().all() == ["precious"]


async def test_a_partially_applied_upgrade_cannot_be_rolled_back_and_says_to_upgrade_forward(
        data_dir, session_factory, db_path, clean_registry):  # noqa: F811
    await _install(session_factory, "1.0.0", V1_MIGRATIONS)
    await _restart(session_factory, db_path)
    await _install(session_factory, "1.1.0", V2_PARTIAL)                                  # v2 applies, then v3 fails

    _, errors = await _restart(session_factory, db_path)

    assert "migration v3" in errors["lc_up"]
    assert (await _row(session_factory))[0] == "error"
    async with session_factory() as s:
        with pytest.raises(installer.InstallError, match=r"migration\(s\) \[2\].*rollback across a migration is refused"):
            await installer.rollback(s, "lc_up", actor="a")
    fixed = V2_PARTIAL.replace('"SELECT * FROM does_not_exist"', '"SELECT 1"')              # the way out is a fixed newer version
    await _install(session_factory, "1.1.1", fixed)
    _, errors = await _restart(session_factory, db_path)
    assert errors == {} and (await _row(session_factory))[:2] == ("active", "1.1.1")


async def test_an_upgrade_whose_code_fails_to_load_is_reported_and_rolls_back(data_dir, session_factory, db_path, clean_registry):  # noqa: F811
    await _install(session_factory, "1.0.0", V1_MIGRATIONS)
    await _restart(session_factory, db_path)
    await _install(session_factory, "1.1.0", V1_MIGRATIONS)
    broken = installer.version_dir("lc_up", "1.1.0") / "lc_up" / "__init__.py"
    broken.write_text("raise RuntimeError('half-written release')\n")                      # passes staging; fails at the real load

    report, errors = await _restart(session_factory, db_path)

    assert errors == {} and "half-written release" in report.errors["lc_up"]
    assert (await _row(session_factory))[:3] == ("error", "1.1.0", "1.0.0")
    async with session_factory() as s:
        await installer.rollback(s, "lc_up", actor="a")
    report, _ = await _restart(session_factory, db_path)
    assert report.loaded == {"lc_up": "1.0.0"} and (await _row(session_factory))[0] == "active"


async def test_a_package_that_does_not_ship_migrations_the_database_already_has_is_refused(data_dir, session_factory, clean_registry):  # noqa: F811
    await _install(session_factory, "1.0.0", V2_PARTIAL.replace('"SELECT * FROM does_not_exist"', '"SELECT 1"'))
    async with session_factory() as s:
        await s.execute(text("INSERT INTO plugin_schema_versions (plugin_id, version, name) VALUES ('lc_up', 1, 'a'), "
                             "('lc_up', 2, 'b'), ('lc_up', 3, 'c')"))
        await s.commit()
    staged = await installer.stage_upload(_chunks(_pkg("0.9.0", V1_MIGRATIONS)), "old.zip")        # an older release: ships only v1

    async with session_factory() as s:
        with pytest.raises(installer.InstallError, match=r"does not ship migration\(s\) \[2, 3\].*downgrade across a migration"):
            await installer.commit(s, staged.token, actor="a")

    async with session_factory() as s:
        assert (await s.get(InstalledPlugin, "lc_up")).version == "1.0.0"                  # the row was not touched
    assert not installer.version_dir("lc_up", "0.9.0").exists()                              # and nothing was moved into place
