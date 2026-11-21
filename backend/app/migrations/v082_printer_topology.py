"""Persist topology and the exact topology used to prepare a print."""
from sqlalchemy import text
version = 82
name = 'printer_topology'
COLUMNS = {'printers': {'topology': 'JSON', 'topology_config': 'JSON', 'topology_error': 'TEXT', 'topology_revision': 'INTEGER NOT NULL DEFAULT 0'}, 'job_printer_configs': {'topology_revision': 'INTEGER'}, 'sliced_versions': {'nozzle_sizes_mm': 'JSON'}}


async def up(conn):
    for table, columns in COLUMNS.items():
        existing = {r[1] for r in (await conn.execute(text(f'PRAGMA table_info({table})'))).fetchall()}
        for name, definition in columns.items():
            if name not in existing:
                await conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))


async def down(conn):
    for table, columns in COLUMNS.items():
        existing = {r[1] for r in (await conn.execute(text(f'PRAGMA table_info({table})'))).fetchall()}
        for name in columns:
            if name in existing:
                await conn.execute(text(f'ALTER TABLE {table} DROP COLUMN {name}'))
