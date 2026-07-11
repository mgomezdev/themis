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
    from .migrations.runner import run_migrations
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await run_migrations(conn)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
