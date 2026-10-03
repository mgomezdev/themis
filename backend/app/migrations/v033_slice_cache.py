"""Slicing cache (BIZ-191): sliced_versions + job/queue/file columns it needs."""
from __future__ import annotations
from sqlalchemy import text

version = 33
name = "slice_cache"

_JOB_COLUMNS = {
    "save_slice": "BOOLEAN NOT NULL DEFAULT 0",
    "save_slice_name": "VARCHAR(255)",
    "allow_cached_slice": "BOOLEAN NOT NULL DEFAULT 0",
    "sliced_version_id": "INTEGER",
    "slice_cache_info": "JSON",
}


async def _columns(conn, table: str) -> set[str]:
    return {row[1] for row in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS sliced_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL UNIQUE REFERENCES uploaded_files(id) ON DELETE CASCADE,
            source_file_id INTEGER REFERENCES uploaded_files(id) ON DELETE SET NULL,
            source_content_hash VARCHAR(64) NOT NULL DEFAULT '',
            plate_number INTEGER NOT NULL DEFAULT 1,
            machine_preset VARCHAR(255) NOT NULL,
            process_preset VARCHAR(512) NOT NULL,
            filament_presets JSON NOT NULL,
            extra_config JSON NOT NULL,
            tool_index INTEGER,
            filament_map JSON,
            artifact_kind VARCHAR(16) NOT NULL,
            cache_key VARCHAR(64) NOT NULL,
            preset_content_hash VARCHAR(64),
            slicer_version VARCHAR(64),
            filament_type VARCHAR(100) NOT NULL DEFAULT 'any',
            filament_color VARCHAR(20) NOT NULL DEFAULT 'any',
            estimated_seconds INTEGER,
            filament_grams FLOAT,
            filament_breakdown JSON,
            created_from_job_id INTEGER,
            created_at VARCHAR(32) NOT NULL
        )
    """))
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_sliced_versions_source_file_id ON sliced_versions (source_file_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sliced_versions_cache_key ON sliced_versions (cache_key)"))

    have = await _columns(conn, "jobs")
    for col, ddl in _JOB_COLUMNS.items():
        if col not in have:
            await conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}"))
    if "slice_cache_use_latest_settings" not in await _columns(conn, "queue_config"):
        await conn.execute(text(
            "ALTER TABLE queue_config ADD COLUMN slice_cache_use_latest_settings BOOLEAN NOT NULL DEFAULT 1"))
    if "pack_recipe_hash" not in await _columns(conn, "uploaded_files"):
        await conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN pack_recipe_hash VARCHAR(64)"))


async def down(conn) -> None:
    await conn.execute(text("ALTER TABLE uploaded_files DROP COLUMN pack_recipe_hash"))
    await conn.execute(text("ALTER TABLE queue_config DROP COLUMN slice_cache_use_latest_settings"))
    for col in reversed(list(_JOB_COLUMNS)):
        await conn.execute(text(f"ALTER TABLE jobs DROP COLUMN {col}"))
    await conn.execute(text("DROP INDEX IF EXISTS ix_sliced_versions_cache_key"))
    await conn.execute(text("DROP INDEX IF EXISTS ix_sliced_versions_source_file_id"))
    await conn.execute(text("DROP TABLE IF EXISTS sliced_versions"))
