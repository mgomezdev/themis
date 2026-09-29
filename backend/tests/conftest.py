import io
import json
import zipfile
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio
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
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Session factory bound to the per-test in-memory DB (the same DB the `client` fixture serves).
    Use it to seed or inspect rows directly: `async with session_factory() as s: ...`."""
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncGenerator[AsyncClient, None]:
    factory = session_factory

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


# ---------------------------------------------------------------------------
# Shared factories for API tests (previously copy-pasted per test file)
# ---------------------------------------------------------------------------

def make_3mf_bytes() -> bytes:
    """Smallest 3MF the upload route accepts: one plate with a 60s / 5g estimate and a thumbnail."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/slice_info.config", json.dumps({
            "plate": [{"index": 1, "prediction": 60, "weight": [5.0]}]
        }))
        zf.writestr("Metadata/plate_1.png", b"\x89PNG")
    return buf.getvalue()


@pytest.fixture
def make_3mf():
    return make_3mf_bytes


@pytest_asyncio.fixture
async def upload_3mf(client, tmp_path):
    """`await upload_3mf(filename="m.3mf", data=None)` -> uploaded file id (library dirs point at tmp_path)."""
    async def _upload(filename: str = "m.3mf", data: bytes | None = None) -> int:
        with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
             patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"):
            (tmp_path / "library").mkdir(exist_ok=True)
            (tmp_path / "filecache").mkdir(exist_ok=True)
            resp = await client.post(
                "/api/v1/files/upload",
                files={"file": (filename, data if data is not None else make_3mf_bytes(), "application/octet-stream")},
            )
        assert resp.status_code in (200, 201), resp.text
        return resp.json()["id"]
    return _upload


@pytest_asyncio.fixture
async def create_printer(client):
    """`await create_printer(**overrides)` -> printer id (a Bambu with an active OrcaSlicer preset)."""
    async def _create(**overrides) -> int:
        body = {
            "name": "P1S", "printer_type": "bambu",
            "connection_config": {},
            "orca_printer_profiles": ["Bambu Lab P1S 0.4"],
            "current_orca_printer_profile": "Bambu Lab P1S 0.4",
        }
        body.update(overrides)
        resp = await client.post("/api/v1/printers", json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]
    return _create


@pytest_asyncio.fixture
async def create_job(client, upload_3mf, create_printer):
    """`await create_job(file_id=None, printer_id=None, order_id=None, **config_overrides)` -> job id.
    Uploads a file / creates a printer when not given. The queue engine is patched out (no wake side effects)."""
    async def _create(file_id: int | None = None, printer_id: int | None = None, order_id: int | None = None,
                      **config_overrides) -> int:
        file_id = file_id if file_id is not None else await upload_3mf()
        printer_id = printer_id if printer_id is not None else await create_printer()
        config = {
            "printer_id": printer_id, "print_profile": "0.20mm",
            "filament_type": "any", "filament_color": "any",
        }
        config.update(config_overrides)
        body = {"uploaded_file_id": file_id, "plate_number": 1, "printer_configs": [config]}
        if order_id is not None:
            body["order_id"] = order_id
        with patch("app.api.routes.jobs.queue_engine"):
            resp = await client.post("/api/v1/jobs", json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]
    return _create


@pytest.fixture
def spoolman_upstream():
    """Point every httpx client the Spoolman service opens at a fake upstream.

    `up.handler = fn(request) -> httpx.Response` (or raise an httpx error) overrides the default,
    which is the real `tests/spoolman_mock.py` ASGI app. `up.requests` records what the service sent.
    Real httpx machinery runs either way, so URL building, headers and HTTP-status errors are genuine.
    """
    import httpx
    from types import SimpleNamespace
    from tests import spoolman_mock

    real_client = httpx.AsyncClient
    up = SimpleNamespace(handler=None, requests=[])

    async def _record(request: httpx.Request) -> None:  # AsyncClient event hooks must be coroutines
        up.requests.append(request)

    def _factory(*args, **kwargs):
        transport = httpx.MockTransport(up.handler) if up.handler else httpx.ASGITransport(app=spoolman_mock.app)
        kwargs.pop("transport", None)
        hooks = kwargs.pop("event_hooks", {}) or {}
        hooks = {**hooks, "request": [*hooks.get("request", []), _record]}
        return real_client(*args, transport=transport, event_hooks=hooks, **kwargs)

    with patch("app.services.spoolman_service.httpx.AsyncClient", _factory):
        yield up

