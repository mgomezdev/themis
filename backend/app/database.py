import os
from pathlib import Path
from collections.abc import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# Docker sets THEMIS_DATA_DIR=/data; locally fall back to <repo-root>/data
_default_data_dir = Path(__file__).resolve().parent.parent.parent / "data"
_data_dir = os.environ.get("THEMIS_DATA_DIR", str(_default_data_dir))
Path(_data_dir).mkdir(parents=True, exist_ok=True)
DATABASE_URL = f"sqlite+aiosqlite:///{_data_dir}/themis.db"

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.run_sync(Base.metadata.create_all)
        await _migrate(conn)


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
        ("block_reason", "TEXT"),
        ("order_id",     "INTEGER"),
        ("overrides",    "JSON"),
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
]


async def _migrate(conn) -> None:
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


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
