"""Initial schema — wraps the existing Base.metadata.create_all + column alters."""
from __future__ import annotations
from sqlalchemy import text

version = 1
name = "initial_schema"

_ALTERS: list[tuple[str, list[tuple[str, str]]]] = [
    ("printers", [
        ("loaded_filaments",         "JSON DEFAULT '[]'"),
        ("queue_on",                 "BOOLEAN NOT NULL DEFAULT 1"),
        ("build_plate_type",         "VARCHAR(100)"),
        ("no_snapshots_while_idle",  "BOOLEAN NOT NULL DEFAULT 0"),
    ]),
    ("job_printer_configs", [
        ("filament_id",    "INTEGER"),
        ("filament_type",  "VARCHAR(100)"),
        ("filament_color", "VARCHAR(20)"),
        ("tool_index",     "INTEGER"),
        ("filament_map",   "JSON"),
    ]),
    ("jobs", [
        ("block_reason",             "TEXT"),
        ("order_id",                 "INTEGER"),
        ("overrides",                "JSON"),
        ("project_id",               "INTEGER"),
        ("completed_at",             "VARCHAR(32)"),
        ("outcome",                  "VARCHAR(20)"),
        ("project_item_quantities",  "TEXT"),
    ]),
    ("uploaded_files", [
        ("relative_path", "VARCHAR(1024) DEFAULT ''"),
        ("folder",        "VARCHAR(1024) DEFAULT '/'"),
        ("size_bytes",    "INTEGER DEFAULT 0"),
        ("content_hash",  "VARCHAR(64) DEFAULT ''"),
        ("mtime",         "FLOAT DEFAULT 0"),
        ("missing",       "BOOLEAN NOT NULL DEFAULT 0"),
    ]),
    ("queue_config", [
        ("operator_name",             "VARCHAR(120)"),
        ("snapshot_interval_seconds", "INTEGER DEFAULT 2"),
    ]),
    ("projects", [
        ("source_app",       "VARCHAR(50)"),
        ("source_user",      "VARCHAR(255)"),
        ("source_layout_id", "INTEGER"),
    ]),
    ("project_items", [
        ("quantity_completed", "INTEGER DEFAULT 0"),
        ("quantity_failed",    "INTEGER DEFAULT 0"),
    ]),
]


async def up(conn) -> None:
    # Create all ORM-managed tables (idempotent via checkfirst=True default)
    from ..database import Base
    await conn.run_sync(Base.metadata.create_all)

    # ADD COLUMN alters for columns added after initial release
    for table, columns in _ALTERS:
        cols = {row[1] for row in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}
        if not cols:
            continue
        for col, typedef in columns:
            if col not in cols:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}"))

    # filament_profile changed NOT NULL → nullable; SQLite can't ALTER COLUMN so recreate.
    jpc_info = (await conn.execute(text("PRAGMA table_info(job_printer_configs)"))).fetchall()
    if {row[1]: row[3] for row in jpc_info}.get("filament_profile", 0) == 1:
        await conn.execute(text("""
            CREATE TABLE job_printer_configs_new (
                id INTEGER NOT NULL,
                job_id INTEGER NOT NULL,
                printer_id INTEGER NOT NULL,
                print_profile VARCHAR(512) NOT NULL,
                filament_profile VARCHAR(512),
                slice_failed BOOLEAN NOT NULL DEFAULT 0,
                slice_error TEXT,
                filament_type VARCHAR(100),
                filament_color VARCHAR(20),
                filament_id INTEGER,
                tool_index INTEGER,
                filament_map JSON,
                PRIMARY KEY (id),
                FOREIGN KEY(job_id) REFERENCES jobs (id),
                FOREIGN KEY(printer_id) REFERENCES printers (id)
            )
        """))
        await conn.execute(text("INSERT INTO job_printer_configs_new SELECT * FROM job_printer_configs"))
        await conn.execute(text("DROP TABLE job_printer_configs"))
        await conn.execute(text("ALTER TABLE job_printer_configs_new RENAME TO job_printer_configs"))

    # machine_uuid/process_uuid changed to nullable; SQLite can't ALTER COLUMN so recreate.
    proj_info = (await conn.execute(text("PRAGMA table_info(projects)"))).fetchall()
    proj_cols = {row[1]: row[3] for row in proj_info}
    if proj_cols.get("machine_uuid", 0) == 1 or proj_cols.get("process_uuid", 0) == 1:
        await conn.execute(text("""
            CREATE TABLE projects_new (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                machine_uuid VARCHAR(36),
                process_uuid VARCHAR(36),
                notes TEXT,
                result_file_id INTEGER REFERENCES uploaded_files (id) ON DELETE SET NULL,
                source_app VARCHAR(50),
                source_user VARCHAR(255),
                source_layout_id INTEGER,
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL
            )
        """))
        await conn.execute(text(
            "INSERT INTO projects_new "
            "(id, name, machine_uuid, process_uuid, notes, result_file_id, created_at, updated_at) "
            "SELECT id, name, machine_uuid, process_uuid, notes, result_file_id, created_at, updated_at "
            "FROM projects"
        ))
        await conn.execute(text("DROP TABLE projects"))
        await conn.execute(text("ALTER TABLE projects_new RENAME TO projects"))


async def down(conn) -> None:
    # Dropping all tables is destructive — only use in dev/test
    from ..database import Base
    await conn.run_sync(Base.metadata.drop_all)
