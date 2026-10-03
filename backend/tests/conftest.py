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
from sqlalchemy import event
from app.database import Base, get_session, _set_sqlite_pragmas
from app.auth import SCOPES
from app.models import ApiKey
from app.services import thumbnail_regen
from app.services.printer_manager import printer_manager
from app.services.api_key_service import generate_key, hash_key

@pytest.fixture(scope="session")
def _schema_template(tmp_path_factory):
    """A migrated-schema SQLite file created once per run; each test gets its own copy (cheap)."""
    import asyncio
    path = tmp_path_factory.mktemp("schema") / "template.db"

    async def build():
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(build())
    return path


@pytest_asyncio.fixture
async def session_factory(tmp_path, _schema_template) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Session factory bound to a per-test SQLite FILE (the same DB the `client` fixture serves), set up the
    way production is: the app's connect pragmas (foreign keys ON, busy_timeout) and a separate connection per
    session, so uncommitted writes are isolated and the queue loop can run alongside a request as it does for
    real. (`sqlite+aiosqlite:///:memory:` shares ONE connection between all sessions: a polling reader sees
    and rolls back another session's uncommitted writes, and FKs are off.)
    Use it to seed or inspect rows directly: `async with session_factory() as s: ...`."""
    import shutil
    db_file = tmp_path / "test.db"
    shutil.copyfile(_schema_template, db_file)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    event.listens_for(engine.sync_engine, "connect")(_set_sqlite_pragmas)
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

def make_sliced_archive(plates=((1, 4.0, 10), (2, 7.5, 20)), slice_info: bool = False) -> bytes:
    """A Bambu-style sliced archive (.gcode.3mf, BIZ-190): `Metadata/plate_N.gcode` per (plate, grams, minutes), a
    thumbnail for plate 1 and — with `slice_info` — a `Metadata/slice_info.config` carrying the same estimates. Deflated
    like real archives; fixed zip timestamps so the content hash is stable."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        def put(name, data):
            zf.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), data, compress_type=zipfile.ZIP_DEFLATED)
        for num, grams, mins in plates:
            put(f"Metadata/plate_{num}.gcode",
                f"; filament used [g] = {grams}\n; estimated printing time (normal mode) = {mins}m 0s\nG28\n")
        put("Metadata/plate_1.png", b"\x89PNG-archive")
        if slice_info:
            body = "".join(
                f'<plate><metadata key="index" value="{n}"/><metadata key="prediction" value="{m * 60}"/>'
                f'<metadata key="weight" value="{g}"/></plate>' for n, g, m in plates)
            put("Metadata/slice_info.config", f'<?xml version="1.0" encoding="UTF-8"?><config>{body}</config>')
    return buf.getvalue()


def make_3mf_bytes() -> bytes:
    """Smallest 3MF the upload route accepts: one plate with a 60s / 5g estimate and a thumbnail."""
    # Entries carry a FIXED timestamp: zipfile stamps "now" (2-second resolution) by default, so two
    # calls straddling a clock tick produced different bytes -> different content hashes -> the upload
    # route stored the second copy as "m (2).3mf" instead of deduplicating (an intermittent test failure).
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in (
            ("Metadata/slice_info.config", json.dumps({"plate": [{"index": 1, "prediction": 60, "weight": [5.0]}]})),
            ("Metadata/plate_1.png", b"\x89PNG"),
        ):
            zf.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), data)
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


@pytest.fixture(autouse=True)
def _reset_laminus_module_state():
    """laminus.py keeps the catalog cache, health memo and pending remap in module globals that outlive a
    test; restore them so a test that warms the cache (or parks a remap) cannot leak into the next one."""
    import app.api.routes.laminus as laminus
    names = ("_catalog_dict", "_catalog_bytes", "_catalog_fetched_at", "_pending_sync", "_health_memo", "_health_memo_at")
    saved = {n: getattr(laminus, n) for n in names}
    yield
    for n, v in saved.items():
        setattr(laminus, n, v)



@pytest.fixture(autouse=True)
def _fresh_camera_hub():
    """The camera hub is a process-wide singleton (shared streams, snapshot cache keyed by printer id, and ids repeat
    from 1 in every test's fresh DB) — give each test a clean one."""
    from app.services import camera_hub
    camera_hub.hub = camera_hub.CameraHub()
    yield
