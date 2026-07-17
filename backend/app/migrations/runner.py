"""Versioned migration runner for Themis (Flyway-style)."""
from __future__ import annotations
from sqlalchemy import text
from . import v001_initial, v002_project_order_link, v003_webhook_config, v004_gcode_estimates, v005_project_order_merge, v006_project_links, v007_printer_bed_size, v008_job_estimates_and_queue_config, v009_drop_filament_profile_uuid

_MIGRATIONS = sorted(
    [v001_initial, v002_project_order_link, v003_webhook_config, v004_gcode_estimates, v005_project_order_merge, v006_project_links, v007_printer_bed_size, v008_job_estimates_and_queue_config, v009_drop_filament_profile_uuid],
    key=lambda m: m.version,
)

_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
    )
"""


async def _applied_versions(conn) -> set[int]:
    rows = (await conn.execute(text("SELECT version FROM schema_migrations ORDER BY version"))).fetchall()
    return {r[0] for r in rows}


async def run_migrations(conn) -> None:
    await conn.execute(text(_CREATE_TABLE))
    applied = await _applied_versions(conn)
    for m in _MIGRATIONS:
        if m.version in applied:
            continue
        print(f"Applying migration v{m.version}: {m.name}")
        await m.up(conn)
        await conn.execute(
            text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"),
            {"v": m.version, "n": m.name},
        )


async def rollback_last(conn) -> None:
    await conn.execute(text(_CREATE_TABLE))
    row = (await conn.execute(
        text("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1")
    )).fetchone()
    if not row:
        print("Nothing to roll back.")
        return
    version = row[0]
    m = next((x for x in _MIGRATIONS if x.version == version), None)
    if not m:
        raise RuntimeError(f"Migration v{version} not found in _MIGRATIONS")
    print(f"Rolling back v{version}: {m.name}")
    await m.down(conn)
    await conn.execute(text("DELETE FROM schema_migrations WHERE version = :v"), {"v": version})
