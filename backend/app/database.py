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
    cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
    if "loaded_filaments" not in cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN loaded_filaments JSON DEFAULT '[]'"))
    if "queue_on" not in cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN queue_on BOOLEAN NOT NULL DEFAULT 1"))
    if "slicer" not in cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN slicer VARCHAR(20) NOT NULL DEFAULT 'orca'"))

    jpc_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(job_printer_configs)"))).fetchall()}
    if "filament_id" not in jpc_cols:
        await conn.execute(text("ALTER TABLE job_printer_configs ADD COLUMN filament_id INTEGER"))
    if "filament_type" not in jpc_cols:
        await conn.execute(text("ALTER TABLE job_printer_configs ADD COLUMN filament_type VARCHAR(100)"))
    if "filament_color" not in jpc_cols:
        await conn.execute(text("ALTER TABLE job_printer_configs ADD COLUMN filament_color VARCHAR(20)"))

    job_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    if "block_reason" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN block_reason TEXT"))
    if "order_id" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN order_id INTEGER"))


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
