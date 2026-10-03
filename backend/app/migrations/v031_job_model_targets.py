"""job_model_targets (jobs eligible on any printer of a make/model) + job_printer_configs.model_target_id."""
from __future__ import annotations
from sqlalchemy import text

version = 31
name = "job_model_targets"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS job_model_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            machine_profile VARCHAR(255) NOT NULL,
            print_profile VARCHAR(512) NOT NULL,
            filament_profile VARCHAR(512),
            filament_id INTEGER,
            filament_type VARCHAR(100) NOT NULL DEFAULT 'any',
            filament_color VARCHAR(20) NOT NULL DEFAULT 'any',
            filament_map JSON
        )
    """))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_model_targets_job_id ON job_model_targets (job_id)"))
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_job_model_targets_machine_profile ON job_model_targets (machine_profile)"))
    cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(job_printer_configs)"))).fetchall()}
    if "model_target_id" not in cols:
        await conn.execute(text(
            "ALTER TABLE job_printer_configs ADD COLUMN model_target_id INTEGER"))
    await conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_job_printer_configs_target_printer "
        "ON job_printer_configs (model_target_id, printer_id) WHERE model_target_id IS NOT NULL"))


async def down(conn) -> None:
    await conn.execute(text("DROP INDEX IF EXISTS ux_job_printer_configs_target_printer"))
    await conn.execute(text("DELETE FROM job_printer_configs WHERE model_target_id IS NOT NULL"))
    await conn.execute(text("ALTER TABLE job_printer_configs DROP COLUMN model_target_id"))
    await conn.execute(text("DROP TABLE IF EXISTS job_model_targets"))
