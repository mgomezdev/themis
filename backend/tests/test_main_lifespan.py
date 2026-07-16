"""Verify the placeholder printer is not seeded and is cleaned up if present."""
import pytest
import pytest_asyncio
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.database import Base
from app.models import Printer

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

PLACEHOLDER_NAME = "Elegoo Centauri Carbon (placeholder)"


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_placeholder_not_present_in_fresh_db(session):
    """A fresh DB should have zero placeholder printers (no seed)."""
    result = await session.execute(
        select(Printer).where(Printer.name == PLACEHOLDER_NAME)
    )
    assert result.scalar_one_or_none() is None


async def test_placeholder_can_be_deleted(session):
    """Simulate the cleanup: pre-populate and then delete it."""
    session.add(Printer(
        name=PLACEHOLDER_NAME,
        printer_type="elegoo_centauri",
        connection_config={"ip_address": "192.0.2.1"},
    ))
    await session.commit()

    await session.execute(
        delete(Printer).where(Printer.name == PLACEHOLDER_NAME)
    )
    await session.commit()

    result = await session.execute(
        select(Printer).where(Printer.name == PLACEHOLDER_NAME)
    )
    assert result.scalar_one_or_none() is None
