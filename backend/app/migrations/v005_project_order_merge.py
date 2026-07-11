"""Merge order fields into projects; replace OrcaSlicer filament profile with type/color/id spec."""
from __future__ import annotations
from sqlalchemy import text

version = 5
name = "project_order_merge"


async def up(conn) -> None:
    for sql in [
        "ALTER TABLE projects ADD COLUMN customer TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE projects ADD COLUMN order_type TEXT NOT NULL DEFAULT 'internal'",
        "ALTER TABLE projects ADD COLUMN on_hold BOOLEAN NOT NULL DEFAULT 0",
        "ALTER TABLE projects ADD COLUMN due_date TEXT",
        "ALTER TABLE project_items ADD COLUMN filament_type TEXT NOT NULL DEFAULT 'any'",
        "ALTER TABLE project_items ADD COLUMN filament_color TEXT NOT NULL DEFAULT 'any'",
        "ALTER TABLE project_items ADD COLUMN filament_id INTEGER",
    ]:
        try:
            await conn.execute(text(sql))
        except Exception:
            pass  # column already exists


async def down(conn) -> None:
    # SQLite doesn't support DROP COLUMN — recreate without the new columns
    await conn.execute(text("""
        CREATE TABLE projects_tmp AS
        SELECT id, name, machine_uuid, process_uuid, notes, result_file_id,
               order_id, source_app, source_user, source_layout_id, created_at, updated_at
        FROM projects
    """))
    await conn.execute(text("DROP TABLE projects"))
    await conn.execute(text("ALTER TABLE projects_tmp RENAME TO projects"))

    await conn.execute(text("""
        CREATE TABLE project_items_tmp AS
        SELECT id, project_id, file_id, quantity, quantity_completed,
               quantity_failed, filament_profile_uuid, color_hex, sort_order
        FROM project_items
    """))
    await conn.execute(text("DROP TABLE project_items"))
    await conn.execute(text("ALTER TABLE project_items_tmp RENAME TO project_items"))
