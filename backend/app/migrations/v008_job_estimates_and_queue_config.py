"""Add estimate/actual columns to jobs and estimates_enabled to queue_config."""
from __future__ import annotations
from sqlalchemy import text

version = 8
name = "job_estimates_and_queue_config"


async def up(conn) -> None:
    job_cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    qc_cols  = {r[1] for r in (await conn.execute(text("PRAGMA table_info(queue_config)"))).fetchall()}

    # Actual values (captured at production slice time, before GcodeFile is deleted)
    if "actual_filament_grams" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN actual_filament_grams REAL"))
    if "actual_seconds" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN actual_seconds INTEGER"))
    if "actual_filament_breakdown" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN actual_filament_breakdown JSON"))
    if "deduction_skipped" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN deduction_skipped BOOLEAN"))

    # Estimate values (from background test slice)
    if "estimate_token" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN estimate_token INTEGER NOT NULL DEFAULT 0"))
    if "estimate_status" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN estimate_status TEXT"))
    if "estimate_seconds" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN estimate_seconds INTEGER"))
    if "estimate_filament_grams" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN estimate_filament_grams REAL"))
    if "estimate_filament_breakdown" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN estimate_filament_breakdown JSON"))
    if "estimate_preset_label" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN estimate_preset_label JSON"))

    # QueueConfig extension
    if "estimates_enabled" not in qc_cols:
        await conn.execute(text(
            "ALTER TABLE queue_config ADD COLUMN estimates_enabled BOOLEAN NOT NULL DEFAULT 0"
        ))


async def down(conn) -> None:
    # SQLite <3.35 cannot DROP COLUMN; recreate jobs without new columns.
    # Not intended for production rollback — only satisfies rollback_last().
    await conn.execute(text("""
        CREATE TABLE jobs_new AS
        SELECT id, uploaded_file_id, plate_number, order_id, assigned_printer_id,
               queue_position, status, project_id, block_reason, overrides,
               created_at, updated_at, completed_at, outcome, project_item_quantities
        FROM jobs
    """))
    await conn.execute(text("DROP TABLE jobs"))
    await conn.execute(text("ALTER TABLE jobs_new RENAME TO jobs"))
