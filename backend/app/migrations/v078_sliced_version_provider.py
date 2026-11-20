"""Record the producing plugin for saved slices."""
from sqlalchemy import text

from .v051_slice_cache_provider_keys import PROVIDER, _key

version = 78
name = "sliced_version_provider"


async def _has_column(conn) -> bool:
    return any(row[1] == "provider_plugin_id"
               for row in (await conn.execute(text("PRAGMA table_info(sliced_versions)"))).fetchall())


async def up(conn) -> None:
    if not await _has_column(conn):
        await conn.execute(text("ALTER TABLE sliced_versions ADD COLUMN provider_plugin_id VARCHAR(80)"))
    # Attribute old slices to Laminus only when the provider-bearing key confirms it.
    rows = (await conn.execute(text(
        "SELECT id, cache_key, source_content_hash, plate_number, machine_preset, process_preset, "
        "filament_presets, extra_config, tool_index, filament_map, artifact_kind "
        "FROM sliced_versions WHERE provider_plugin_id IS NULL"))).fetchall()
    known = [{"id": row.id, "provider": PROVIDER} for row in rows if row.cache_key == _key(row, with_provider=True)]
    if known:
        await conn.execute(text("UPDATE sliced_versions SET provider_plugin_id = :provider WHERE id = :id"), known)


async def down(conn) -> None:
    if await _has_column(conn):
        await conn.execute(text("ALTER TABLE sliced_versions DROP COLUMN provider_plugin_id"))
