import pytest_asyncio
from collections.abc import AsyncGenerator
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.main import app
from app.database import Base, get_session
from app.auth import SCOPES
from app.models import ApiKey
from app.services import thumbnail_regen
from app.services.printer_manager import printer_manager
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

    original_thumbnail_factory = thumbnail_regen._session_factory
    thumbnail_regen.set_session_factory(factory)

    try:
        # Seed a full-scope API key so every call site has a credential
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
    finally:
        app.dependency_overrides.clear()
        thumbnail_regen.set_session_factory(original_thumbnail_factory)
        await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _reset_login_throttle():
    """Failed-login throttle is process-global; don't let one test's failures leak into another."""
    from app.services import login_throttle
    login_throttle.reset()
    yield
    login_throttle.reset()


@pytest_asyncio.fixture(autouse=True)
async def _isolate_printer_manager():
    """The module-level printer_manager singleton outlives every test, and printer ids collide
    (each test's in-memory DB starts at id 1). Routes call printer_manager.connect_printer(), which
    would start a REAL vendor client (threads + LAN connection attempts) and leak it into later tests.
    Register clients without connecting them, and reset the singleton around every test."""
    def _clear():
        printer_manager._clients.clear()
        printer_manager._awaiting_plate_clear.clear()

    _clear()
    printer_manager.connect_printer = lambda printer_id, client: printer_manager.register_client(printer_id, client)  # type: ignore[method-assign]
    try:
        yield
    finally:
        printer_manager.__dict__.pop("connect_printer", None)
        _clear()
