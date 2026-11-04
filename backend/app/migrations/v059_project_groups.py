"""Print groups (BIZ-360): project_groups + project_group_targets, project_items.group_id, and a Default Group for every existing
project holding all of its items. Idempotent."""
from __future__ import annotations
from sqlalchemy import text

version = 59
name = "project_groups"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS project_groups (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_default BOOLEAN NOT NULL DEFAULT 0,
            strategy VARCHAR(20) NOT NULL DEFAULT 'fixed',
            envelope_x FLOAT,
            envelope_y FLOAT,
            mix_colours BOOLEAN NOT NULL DEFAULT 0
        )"""))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_project_groups_project_id ON project_groups (project_id)"))
    await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_project_groups_one_default ON project_groups (project_id) "
                            "WHERE is_default = 1"))
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS project_group_targets (
            id INTEGER PRIMARY KEY,
            group_id INTEGER NOT NULL REFERENCES project_groups(id) ON DELETE CASCADE,
            target_key VARCHAR(64) NOT NULL,
            process_profile VARCHAR(512) NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            UNIQUE (group_id, target_key)
        )"""))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_project_group_targets_group_id ON project_group_targets (group_id)"))
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(project_items)"))).fetchall()}
    if "group_id" not in cols:
        await conn.execute(text(
            "ALTER TABLE project_items ADD COLUMN group_id INTEGER"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_project_items_group_id ON project_items (group_id)"))
    # Backfill: one Default Group per project that has none, holding the items that have no group.
    await conn.execute(text(
        "INSERT INTO project_groups (project_id, name, sort_order, is_default) "
        "SELECT p.id, 'Default Group', 0, 1 FROM projects p "
        "WHERE NOT EXISTS (SELECT 1 FROM project_groups g WHERE g.project_id = p.id AND g.is_default = 1)"))
    await conn.execute(text(
        "UPDATE project_items SET group_id = (SELECT g.id FROM project_groups g WHERE g.project_id = project_items.project_id "
        "AND g.is_default = 1) WHERE group_id IS NULL"))


async def down(conn) -> None:
    await conn.execute(text("DROP INDEX IF EXISTS ix_project_items_group_id"))
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(project_items)"))).fetchall()}
    if "group_id" in cols:
        await conn.execute(text("ALTER TABLE project_items DROP COLUMN group_id"))
    await conn.execute(text("DROP TABLE IF EXISTS project_group_targets"))
    await conn.execute(text("DROP TABLE IF EXISTS project_groups"))
