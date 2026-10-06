"""Startup loading of installed plugins (spec §3.11). Runs at import time (routers must be mounted before the app starts),
after the bundled plugins. For each `installed_plugins` row: put the package dir and its `vendor/` on `sys.path` AFTER
Themis's own site-packages (a plugin cannot shadow Themis's libraries), import the entry, check the toml and MANIFEST
agree, register it. ANY failure is contained: that plugin is reported as `error`; Themis always boots."""
from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import PluginError, bundled_ids, register_plugin
from .package import check_matches, entry_file_exists, read_toml
from .manifest import ID_RE

logger = logging.getLogger("app")


@dataclass
class LoadReport:
    loaded: dict[str, str] = field(default_factory=dict)       # id -> version now running
    errors: dict[str, str] = field(default_factory=dict)       # id -> why it did not load
    removed: list[str] = field(default_factory=list)           # ids whose code was deleted (pending_removal)


last_report = LoadReport()


def _rows(db_path: Path) -> list[tuple[str, str, str]]:
    """(plugin_id, version, status) from the database, read synchronously: the app's async engine is not up at import time.
    A missing database/table (fresh install, before migrations) is simply no installed plugins."""
    if not db_path.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            return [(r[0], r[1], r[2]) for r in con.execute("SELECT plugin_id, version, status FROM installed_plugins")]
        finally:
            con.close()
    except sqlite3.Error:
        return []


def _purge(top: str) -> None:
    for name in [n for n in sys.modules if n == top or n.startswith(top + ".")]:
        del sys.modules[name]


def _load_one(plugins_dir: Path, plugin_id: str, version: str, reserved: frozenset[str]) -> None:
    import importlib
    from .installer import _taken            # local: installer imports this package's siblings
    if not ID_RE.match(plugin_id) or not re.match(r"^[0-9][0-9A-Za-z.-]*$", version):
        raise PluginError("invalid plugin id or version")
    if plugin_id in reserved:
        raise PluginError("id is reserved by a bundled plugin")
    root = plugins_dir / plugin_id / version
    t = read_toml(root)
    if t.id != plugin_id or t.version != version:
        raise PluginError(f"themis-plugin.toml says {t.id} {t.version}, expected {plugin_id} {version}")
    if not entry_file_exists(root, t):
        raise PluginError(f"entry module {t.module!r} not found")
    if _taken(t.top_package, plugin_id, plugins_dir):
        raise PluginError(f"top-level package name {t.top_package!r} is already used")
    added = [str(root), str(root / "vendor")]
    sys.path.extend(added)                                # appended: Themis's own packages always win
    try:
        manifest = getattr(importlib.import_module(t.module), t.attr)
        check_matches(t, manifest)
        if any(tab.renderer == "component" for tab in manifest.ui.tabs):
            raise PluginError("`component` tabs are for bundled plugins; installed plugins get `default` and `schema` tabs")
        if manifest.alias_routers:
            raise PluginError("alias_routers (absolute paths) are for bundled plugins")
        register_plugin(manifest)
    except BaseException:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)
        _purge(t.top_package)
        raise


def load_installed(plugins_dir: Path, db_path: Path) -> LoadReport:
    """Load every installed plugin that is due. Also: wipe leftover staging, delete the code of `pending_removal` plugins."""
    global last_report
    report = LoadReport()
    last_report = report
    shutil.rmtree(plugins_dir / ".staging", ignore_errors=True)       # an install interrupted by a restart
    reserved = bundled_ids()
    for plugin_id, version, status in _rows(db_path):
        if status == "pending_removal":
            if ID_RE.match(plugin_id):
                shutil.rmtree(plugins_dir / plugin_id, ignore_errors=True)
            report.removed.append(plugin_id)
            continue
        try:
            _load_one(plugins_dir, plugin_id, version, reserved)
            report.loaded[plugin_id] = version
        except (Exception, SystemExit) as e:                         # a broken plugin (even one calling exit) never stops boot
            logger.exception("Installed plugin %s %s failed to load", plugin_id, version)
            report.errors[plugin_id] = f"{type(e).__name__}: {e}"[:500]
    return report


async def reconcile(session_factory, report: LoadReport, migration_errors: dict[str, str]) -> None:
    """After migrations: record each installed plugin's outcome (active / error), and drop the rows of removed ones."""
    from sqlalchemy import select
    from ..models import InstalledPlugin
    async with session_factory() as s:
        for row in (await s.execute(select(InstalledPlugin))).scalars():
            if row.plugin_id in report.removed:
                await s.delete(row)
            elif row.plugin_id in report.errors:
                row.status, row.error = "error", report.errors[row.plugin_id]
            elif row.plugin_id in migration_errors:
                row.status, row.error = "error", migration_errors[row.plugin_id]
            elif row.plugin_id in report.loaded:
                row.status, row.error = "active", None
        await s.commit()
