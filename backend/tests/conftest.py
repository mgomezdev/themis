import pytest_asyncio
from collections.abc import AsyncGenerator
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.main import app
from app.database import Base, get_session
from app.auth import SCOPES
from app.models import ApiKey
from app.services.api_key_service import generate_key, hash_key

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session

    # Seed a full-scope API key so the bootstrap hatch closes deterministically
    # and every existing call site keeps working unmodified (auth is enforced
    # everywhere now — see Task 5).
    raw, prefix = generate_key()
    async with factory() as _seed:
        _seed.add(ApiKey(
            name="test-fixture", key_prefix=prefix, key_hash=hash_key(raw),
            scopes=sorted(SCOPES), enabled=True, created_at="2026-01-01T00:00:00",
        ))
        await _seed.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-Api-Key": raw},
    ) as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()
