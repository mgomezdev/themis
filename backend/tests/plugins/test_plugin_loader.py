"""Startup loading of installed plugins (BIZ-223): bundled-format toml, sys.path order, contained failures, restart-applied
batches, pending removal, status reconciliation."""
import shutil
import sqlite3
import sys

import pytest
from sqlalchemy import select

from app import plugins
from app.models import InstalledPlugin
from app.plugins import get_plugin, load_bundled, loader
from app.plugins.package import check_matches, read_toml
from tests.plugins import pkg_builder as pb


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A data dir with an installed_plugins table; the registry, sys.path and sys.modules are restored afterwards."""
    pdir = tmp_path / "plugins"
    db = tmp_path / "themis.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE installed_plugins (plugin_id TEXT PRIMARY KEY, version TEXT, status TEXT)")
    con.commit()
    saved_reg, saved_path, saved_mods = dict(plugins._REGISTRY), list(sys.path), set(sys.modules)
    class E:
        pass
    e = E()
    e.pdir, e.db, e.con = pdir, db, con

    def put(files: dict, *, status="pending_restart", row=True, version=None):
        toml = files["themis-plugin.toml"]
        pid = toml.split('id = "')[1].split('"')[0] if 'id = "' in toml else "bad_one"          # (a deliberately broken toml)
        ver = version or (toml.split('version = "')[1].split('"')[0] if 'version = "' in toml else "1.0.0")
        d = pdir / pid / ver
        for name, data in files.items():
            (d / name).parent.mkdir(parents=True, exist_ok=True)
            (d / name).write_text(data)
        if row:
            con.execute("INSERT OR REPLACE INTO installed_plugins VALUES (?,?,?)", (pid, ver, status))
            con.commit()
        return pid
    e.put = put
    e.load = lambda: loader.load_installed(pdir, db)
    yield e
    con.close()
    plugins._REGISTRY.clear(); plugins._REGISTRY.update(saved_reg)
    sys.path[:] = saved_path
    for m in set(sys.modules) - saved_mods:
        del sys.modules[m]


def test_bundled_plugins_use_the_same_toml_format_and_agree_with_their_manifest():
    assert load_bundled() == []
    for dotted in plugins.BUNDLED_MODULES:
        mod = __import__(dotted, fromlist=["MANIFEST"])
        from pathlib import Path
        t = read_toml(Path(mod.__file__).parent)
        check_matches(t, mod.MANIFEST)
        assert t.entry == f"{dotted}:MANIFEST"
    assert plugins.bundled_ids() == {"spoolman", "local_inventory", "bambu", "elegoo_centauri", "snapmaker", "mock"}


def test_a_bundled_toml_that_disagrees_with_its_manifest_is_not_loaded(monkeypatch):
    import app.plugins.package as package
    real = package.read_toml
    monkeypatch.setattr(package, "read_toml", lambda d, **k: real(d, **k).__class__(**{**real(d, **k).__dict__, "version": "9.9.9"}))
    saved = dict(plugins._REGISTRY)
    plugins._REGISTRY.clear()
    try:
        assert sorted(load_bundled()) == sorted(plugins.BUNDLED_MODULES)          # every one refused, none registered
        assert plugins._REGISTRY == {}
    finally:
        plugins._REGISTRY.update(saved)


def test_installed_plugin_loads_registers_and_goes_after_themis_on_sys_path(env):
    before = list(sys.path)
    env.put(pb.files("acme_inv"))
    r = env.load()
    assert r.loaded == {"acme_inv": "1.0.0"} and r.errors == {}
    assert get_plugin("acme_inv").version == "1.0.0"
    assert sys.path[: len(before)] == before                                       # never in front of Themis's own packages
    assert sys.path[len(before):] == [str(env.pdir / "acme_inv/1.0.0"), str(env.pdir / "acme_inv/1.0.0/vendor")]


def test_vendor_dir_is_importable_by_the_plugin(env):
    code = "import vend_lib\n" + pb.CODE.format(id="acme_inv", name="A", mversion="1.0.0", mhost=1, mprov=1, tab="default", extra="")
    env.put({**pb.files(code=code), "vendor/vend_lib.py": "X = 1\n"})
    assert env.load().loaded == {"acme_inv": "1.0.0"}


def test_a_vendored_module_cannot_shadow_a_themis_dependency(env):
    code = "import pydantic\nassert hasattr(pydantic, 'BaseModel')\n" + pb.CODE.format(id="acme_inv", name="A", mversion="1.0.0", mhost=1, mprov=1, tab="default", extra="")
    env.put({**pb.files(code=code), "vendor/pydantic.py": "BaseModel = None\n"})
    assert env.load().loaded == {"acme_inv": "1.0.0"}
    import pydantic
    assert pydantic.BaseModel is not None


FAILURES = {
    "import error": (lambda: pb.files("bad_one", code="raise RuntimeError('boom at import')\n"), "boom at import"),
    "missing dependency": (lambda: pb.files("bad_one", code="import not_installed_dep_xyz\n"), "not_installed_dep_xyz"),
    "host_api mismatch": (lambda: pb.files("bad_one", host_api=1, mhost=2), "host_api 2"),
    "toml/manifest disagree": (lambda: pb.files("bad_one", mversion="9.9.9"), "9.9.9"),
    "component tab (bundled only)": (lambda: pb.files("bad_one", tab="component"), "bundled"),
    "alias routers (bundled only)": (lambda: pb.files("bad_one", extra=", alias_routers=(__import__('fastapi').APIRouter(),)"), "alias_routers"),
    "calls sys.exit": (lambda: pb.files("bad_one", code="import sys\nsys.exit(3)\n"), "SystemExit"),
    "broken toml": (lambda: pb.files("bad_one", toml="id = ["), "TOML"),
}


@pytest.mark.parametrize("label", FAILURES)
def test_a_broken_plugin_is_reported_and_never_stops_the_others_or_boot(env, label):
    build, message = FAILURES[label]
    env.put(pb.files("good_one"))
    env.put(build())
    before = list(sys.path)
    r = env.load()
    assert r.loaded == {"good_one": "1.0.0"}
    assert message in r.errors["bad_one"]
    assert get_plugin("bad_one") is None
    assert str(env.pdir / "bad_one/1.0.0") not in sys.path and "bad_one" not in sys.modules       # fully backed out
    assert str(env.pdir / "good_one/1.0.0") in sys.path and sys.path[: len(before)] == before


def test_missing_directory_reserved_id_and_name_collisions_are_errors_not_crashes(env):
    env.con.execute("INSERT INTO installed_plugins VALUES ('ghost_one', '1.0.0', 'active')")      # row but no files
    env.con.execute("INSERT INTO installed_plugins VALUES ('spoolman', '1.0.0', 'active')")       # claims a bundled id
    env.con.commit()
    env.put(pb.files("clash_one", pkg="json"))                                                    # shadows the stdlib
    r = env.load()
    assert r.loaded == {}
    assert set(r.errors) == {"ghost_one", "spoolman", "clash_one"}
    assert "reserved" in r.errors["spoolman"] and "already used" in r.errors["clash_one"]


def test_two_plugins_cannot_share_a_top_level_package(env):
    env.put(pb.files("first_one", pkg="shared_pkg"))
    env.put(pb.files("second_one", pkg="shared_pkg"))
    r = env.load()
    assert len(r.loaded) == 1 and len(r.errors) == 1
    assert "already used" in next(iter(r.errors.values()))


def test_no_database_or_table_means_no_installed_plugins(tmp_path):
    assert loader.load_installed(tmp_path / "plugins", tmp_path / "missing.db").loaded == {}
    bare = tmp_path / "bare.db"
    sqlite3.connect(bare).close()
    assert loader.load_installed(tmp_path / "plugins", bare).loaded == {}


def test_pending_removal_deletes_the_code_and_a_restart_applies_several_changes_at_once(env):
    """The batched restart: install + upgrade + uninstall staged earlier all take effect in ONE load."""
    env.put(pb.files("keep_one", "1.0.0"))
    env.put(pb.files("keep_one", "1.1.0"))                      # upgrade staged (row now points at 1.1.0)
    env.put(pb.files("new_one"))                                # fresh install staged
    env.put(pb.files("gone_one"), status="pending_removal")
    (env.pdir / ".staging" / "abc").mkdir(parents=True)         # an interrupted install
    r = env.load()
    assert r.loaded == {"keep_one": "1.1.0", "new_one": "1.0.0"}
    assert r.removed == ["gone_one"] and not (env.pdir / "gone_one").exists()
    assert get_plugin("keep_one").version == "1.1.0"
    assert not (env.pdir / ".staging").exists()
    assert (env.pdir / "keep_one/1.0.0").is_dir()               # previous version kept for rollback


def test_staged_changes_survive_a_crash_before_the_restart(env):
    """Everything is on disk + in the table before the restart, so an unrelated/crash restart applies them the same way."""
    env.put(pb.files("acme_inv"))
    first = env.load()
    for k in list(plugins._REGISTRY):
        if k == "acme_inv":
            del plugins._REGISTRY[k]
    sys.modules.pop("acme_inv", None)
    second = env.load()                                         # "another process start"
    assert first.loaded == second.loaded == {"acme_inv": "1.0.0"}


async def test_reconcile_records_active_error_and_removes_deleted_rows(env, session_factory):
    async with session_factory() as s:
        for pid, status in (("ok_one", "pending_restart"), ("bad_one", "pending_restart"), ("mig_one", "pending_restart"),
                            ("gone_one", "pending_removal")):
            s.add(InstalledPlugin(plugin_id=pid, version="1.0.0", source="upload", archive_sha256="x", installed_at="t", status=status))
        await s.commit()
    report = loader.LoadReport(loaded={"ok_one": "1.0.0", "mig_one": "1.0.0"}, errors={"bad_one": "ImportError: x"}, removed=["gone_one"])
    await loader.reconcile(session_factory, report, {"mig_one": "migration v1 failed"})
    async with session_factory() as s:
        rows = {r.plugin_id: (r.status, r.error) for r in (await s.execute(select(InstalledPlugin))).scalars()}
    assert rows == {"ok_one": ("active", None), "bad_one": ("error", "ImportError: x"), "mig_one": ("error", "migration v1 failed")}
