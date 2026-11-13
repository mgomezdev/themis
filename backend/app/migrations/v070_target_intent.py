"""Per-target overrides and ordered printer preferences."""
from sqlalchemy import text

version = 70
name = "target_intent"
_COLUMNS = [("jobs","printer_preference","JSON"), ("project_groups","printer_preference","JSON"),
            ("job_printer_configs","overrides","JSON"), ("job_model_targets","overrides","JSON"),
            ("project_group_targets","overrides","JSON"), ("project_group_targets","filament_profile","VARCHAR(255)"),
            ("project_group_targets","filament_map","JSON")]


async def up(conn):
    for table,column,sqltype in _COLUMNS:
        cols = {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}
        if column not in cols:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))


async def down(conn):
    for table,column,_ in reversed(_COLUMNS):
        cols = {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}
        if column in cols:
            await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
