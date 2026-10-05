"""Plugin-owned migrations (spec §3.8): `plugins/<id>/migrations/vNNN_*.py` with the core `version/name/up/down` shape,
tracked in `plugin_schema_versions`. They run after core migrations on startup, only for registered plugins.

* Every table a plugin migration creates must start with the plugin's `table_prefix` (default `<id>_`).
* Failure is contained: one plugin's failing migration is rolled back (savepoint), reported in the returned dict and
  `migrations.failed` (the host then refuses to build that plugin), and never stops Themis or other plugins.
* Only table *creation* outside the prefix is detected; plugins are trusted (spec D17) and may still ALTER core tables."""
from __future__ import annotations

import logging

from sqlalchemy import text

from . import registered_plugins
from .manifest import PluginError, PluginManifest

logger = logging.getLogger("app")

# {plugin_id: error} from the last run_plugin_migrations(); the host refuses to build a plugin listed here, so a
# plugin whose tables are missing is never enabled and called (it shows the error instead).
failed: dict[str, str] = {}

_CREATE = """
    CREATE TABLE IF NOT EXISTS plugin_schema_versions (
        plugin_id VARCHAR(64) NOT NULL,
        version INTEGER NOT NULL,
        name VARCHAR(200) NOT NULL,
        applied_at VARCHAR(32) NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
        PRIMARY KEY (plugin_id, version)
    )
"""


async def _tables(conn) -> set[str]:
    return {r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()}


async def _applied(conn, plugin_id: str) -> set[int]:
    rows = await conn.execute(text("SELECT version FROM plugin_schema_versions WHERE plugin_id = :p"), {"p": plugin_id})
    return {r[0] for r in rows.fetchall()}


async def _apply(conn, m: PluginManifest, mod) -> None:
    before = await _tables(conn)
    await mod.up(conn)
    # SQLite's own bookkeeping tables (`sqlite_sequence` appears with the first AUTOINCREMENT table) are not the plugin's
    stray = sorted(t for t in (await _tables(conn)) - before if not t.startswith(m.prefix) and not t.startswith("sqlite_"))
    if stray:
        raise PluginError(f"plugin {m.id!r} migration v{mod.version} created tables outside its prefix "
                          f"{m.prefix!r}: {stray}")
    await conn.execute(text("INSERT INTO plugin_schema_versions (plugin_id, version, name) VALUES (:p, :v, :n)"),
                       {"p": m.id, "v": mod.version, "n": mod.name})


async def run_plugin_migrations(conn, manifests: list[PluginManifest] | None = None) -> dict[str, str]:
    """Apply pending migrations for each plugin in version order. Returns `{plugin_id: error}` for plugins whose
    migration failed (that migration is rolled back; later versions of the same plugin are not attempted). Also
    publishes the result as `migrations.failed`. Never raises: a plugin problem must not stop Themis."""
    errors: dict[str, str] = {}
    try:
        await conn.execute(text(_CREATE))
        for m in manifests if manifests is not None else registered_plugins():
            try:
                done = await _applied(conn, m.id)
                for mod in sorted(m.migrations, key=lambda x: x.version):
                    if mod.version in done:
                        continue
                    try:
                        async with conn.begin_nested():
                            await _apply(conn, m, mod)
                    except Exception as e:
                        logger.warning("Plugin %s migration v%s failed: %s", m.id, mod.version, e)
                        errors[m.id] = f"migration v{mod.version} ({mod.name}) failed: {e}"
                        break
            except Exception as e:
                logger.warning("Plugin %s migrations could not run: %s", m.id, e)
                errors[m.id] = f"migrations could not run: {e}"
    except Exception as e:                      # e.g. the tracking table itself — every plugin is suspect
        logger.warning("Plugin migrations could not run: %s", e)
        for m in manifests if manifests is not None else registered_plugins():
            errors.setdefault(m.id, f"migrations could not run: {e}")
    failed.clear()
    failed.update(errors)
    return errors


async def rollback_plugin_migration(conn, manifest: PluginManifest) -> int | None:
    """Undo the plugin's newest applied migration; returns its version, or None when nothing was applied."""
    await conn.execute(text(_CREATE))
    done = await _applied(conn, manifest.id)
    if not done:
        return None
    version = max(done)
    mod = next((x for x in manifest.migrations if x.version == version), None)
    if mod is None:
        raise PluginError(f"plugin {manifest.id!r} no longer ships migration v{version}; cannot roll it back")
    async with conn.begin_nested():             # all-or-nothing: a failing down() leaves the version recorded
        await mod.down(conn)
        await conn.execute(text("DELETE FROM plugin_schema_versions WHERE plugin_id = :p AND version = :v"),
                           {"p": manifest.id, "v": version})
    return version
