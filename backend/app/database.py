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


async def _migrate(conn) -> None:
    # Add columns introduced after initial schema without a migration tool.
    # Each block guards on PRAGMA table_info; if the table doesn't exist the
    # PRAGMA returns no rows so the col-set is empty — skip the whole block.
    cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
    if cols:
        if "loaded_filaments" not in cols:
            await conn.execute(text("ALTER TABLE printers ADD COLUMN loaded_filaments JSON DEFAULT '[]'"))
        if "queue_on" not in cols:
            await conn.execute(text("ALTER TABLE printers ADD COLUMN queue_on BOOLEAN NOT NULL DEFAULT 1"))

    jpc_info = (await conn.execute(text("PRAGMA table_info(job_printer_configs)"))).fetchall()
    jpc_cols = {row[1] for row in jpc_info}
    if jpc_cols:
        if "filament_id" not in jpc_cols:
            await conn.execute(text("ALTER TABLE job_printer_configs ADD COLUMN filament_id INTEGER"))
        if "filament_type" not in jpc_cols:
            await conn.execute(text("ALTER TABLE job_printer_configs ADD COLUMN filament_type VARCHAR(100)"))
        if "filament_color" not in jpc_cols:
            await conn.execute(text("ALTER TABLE job_printer_configs ADD COLUMN filament_color VARCHAR(20)"))
        if "tool_index" not in jpc_cols:
            await conn.execute(text("ALTER TABLE job_printer_configs ADD COLUMN tool_index INTEGER"))
        if "filament_map" not in jpc_cols:
            await conn.execute(text("ALTER TABLE job_printer_configs ADD COLUMN filament_map JSON"))

        # filament_profile changed from NOT NULL to nullable; SQLite can't ALTER COLUMN so recreate.
        jpc_notnull = {row[1]: row[3] for row in jpc_info}
        if jpc_notnull.get("filament_profile", 0) == 1:
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
            await conn.execute(text(
                "INSERT INTO job_printer_configs_new SELECT * FROM job_printer_configs"
            ))
            await conn.execute(text("DROP TABLE job_printer_configs"))
            await conn.execute(text(
                "ALTER TABLE job_printer_configs_new RENAME TO job_printer_configs"
            ))

    job_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    if job_cols:
        if "block_reason" not in job_cols:
            await conn.execute(text("ALTER TABLE jobs ADD COLUMN block_reason TEXT"))
        if "order_id" not in job_cols:
            await conn.execute(text("ALTER TABLE jobs ADD COLUMN order_id INTEGER"))
        if "overrides" not in job_cols:
            await conn.execute(text("ALTER TABLE jobs ADD COLUMN overrides JSON"))

    uf_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(uploaded_files)"))).fetchall()}
    if "relative_path" not in uf_cols:
        await conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN relative_path VARCHAR(1024) DEFAULT ''"))
    if "folder" not in uf_cols:
        await conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN folder VARCHAR(1024) DEFAULT '/'"))
    if "size_bytes" not in uf_cols:
        await conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN size_bytes INTEGER DEFAULT 0"))
    if "content_hash" not in uf_cols:
        await conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN content_hash VARCHAR(64) DEFAULT ''"))
    if "mtime" not in uf_cols:
        await conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN mtime FLOAT DEFAULT 0"))
    if "missing" not in uf_cols:
        await conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN missing BOOLEAN NOT NULL DEFAULT 0"))


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
