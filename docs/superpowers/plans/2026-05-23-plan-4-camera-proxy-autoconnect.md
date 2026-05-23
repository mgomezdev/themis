# Plan 4: Camera Proxy + Printer Auto-connect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-connect enabled printers at startup and expose a camera streaming proxy endpoint so the frontend can display live printer feeds.

**Architecture:** A new `connect_all_enabled_printers()` async helper is called from the lifespan after `printer_manager` is wired up. Camera stream URLs are exposed as optional properties on the ABC + vendors. A new `camera_proxy` module handles both MJPEG forwarding (Elegoo via httpx) and RTSP-to-MJPEG conversion (Bambu via ffmpeg subprocess). A single route `GET /api/v1/printers/{id}/camera` streams the response.

**Tech Stack:** FastAPI StreamingResponse, httpx (moved to production deps), asyncio subprocess (ffmpeg), paho-mqtt (Bambu RTSPS), websocket-client (Elegoo)

---

### Task 1: Printer auto-connect at startup

**Files:**
- Modify: `backend/app/services/printer_manager.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_autoconnect.py`

Printers are stored in DB but never connected at startup — `printer_manager._clients` is always empty. This task loads all enabled printers from DB, creates a client for each, and registers it.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_autoconnect.py`:

```python
import pytest
from unittest.mock import MagicMock, patch
from app.services.printer_manager import PrinterManager


@pytest.mark.asyncio
async def test_connect_all_enabled_printers_registers_clients():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from app.database import Base
    from app.models import Printer

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        p1 = Printer(name="P1", printer_type="bambu",
                     connection_config={"ip_address": "1.2.3.4", "serial_number": "SN1", "access_code": "abc"},
                     enabled=True)
        p2 = Printer(name="P2", printer_type="bambu",
                     connection_config={"ip_address": "1.2.3.5", "serial_number": "SN2", "access_code": "def"},
                     enabled=False)
        session.add_all([p1, p2])
        await session.commit()
        await session.refresh(p1)
        await session.refresh(p2)

    pm = PrinterManager()
    pm.set_loop(MagicMock())

    mock_client = MagicMock()
    with patch("app.services.printer_manager.create_client", return_value=mock_client):
        with patch.object(pm, "connect_printer") as mock_connect:
            await pm.connect_all_enabled_printers(factory)
            assert mock_connect.call_count == 1
            assert mock_connect.call_args[0][0] == p1.id

    await engine.dispose()


@pytest.mark.asyncio
async def test_connect_all_skips_erroring_printer():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from app.database import Base
    from app.models import Printer

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        p1 = Printer(name="P1", printer_type="bambu",
                     connection_config={"ip_address": "1.2.3.4", "serial_number": "SN1", "access_code": "abc"},
                     enabled=True)
        p2 = Printer(name="P2", printer_type="bambu",
                     connection_config={"ip_address": "1.2.3.5", "serial_number": "SN2", "access_code": "def"},
                     enabled=True)
        session.add_all([p1, p2])
        await session.commit()
        await session.refresh(p1)
        await session.refresh(p2)

    pm = PrinterManager()
    pm.set_loop(MagicMock())

    connect_calls = []
    def fake_connect(printer_id, client):
        connect_calls.append(printer_id)
        if len(connect_calls) == 1:
            raise RuntimeError("connection refused")

    with patch("app.services.printer_manager.create_client", return_value=MagicMock()):
        with patch.object(pm, "connect_printer", side_effect=fake_connect):
            await pm.connect_all_enabled_printers(factory)
            assert len(connect_calls) == 2

    await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && pytest tests/test_autoconnect.py -v
```

Expected: `AttributeError: 'PrinterManager' object has no attribute 'connect_all_enabled_printers'`

- [ ] **Step 3: Add `connect_all_enabled_printers` to `PrinterManager`**

In `backend/app/services/printer_manager.py`, add import near top:

```python
from .printer_client_factory import create_client
```

Add method to `PrinterManager` after `load_awaiting_plate_clear`:

```python
async def connect_all_enabled_printers(self, session_factory) -> None:
    if not session_factory:
        return
    async with session_factory() as session:
        from ..models import Printer
        result = await session.execute(
            select(Printer).where(Printer.enabled == True)  # noqa: E712
        )
        printers = list(result.scalars().all())
    for printer in printers:
        try:
            client = create_client(printer)
            self.connect_printer(printer.id, client)
        except Exception:
            logger.exception("Failed to connect printer %d (%s)", printer.id, printer.name)
```

- [ ] **Step 4: Wire into lifespan in `main.py`**

In `backend/app/main.py`, add after `await printer_manager.load_awaiting_plate_clear_from_db()`:

```python
await printer_manager.connect_all_enabled_printers(SessionLocal)
```

- [ ] **Step 5: Run tests**

```
cd backend && pytest tests/test_autoconnect.py -v
```

Expected: 2 tests PASS

- [ ] **Step 6: Run full suite**

```
cd backend && pytest -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```
git add backend/app/services/printer_manager.py backend/app/main.py backend/tests/test_autoconnect.py
git commit -m "feat: auto-connect enabled printers at startup"
```

---

### Task 2: Camera stream URL properties on ABC + vendors

**Files:**
- Modify: `backend/app/services/abstract_printer_client.py`
- Modify: `backend/app/services/bambu_mqtt.py`
- Modify: `backend/app/services/elegoo_centauri_client.py`
- Modify: `backend/tests/services/test_bambu_mqtt.py`
- Modify: `backend/tests/services/test_elegoo_centauri.py`

Add `camera_mjpeg_url` and `camera_rtsp_url` optional properties to the ABC (both return `None`) and implement on each vendor.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/services/test_bambu_mqtt.py`:

```python
def test_camera_rtsp_url_returns_rtsps_url():
    client = _make_client()
    assert client.camera_rtsp_url == "rtsps://bblp:12345678@192.168.1.10:322/streaming/live/1"


def test_camera_mjpeg_url_is_none_for_bambu():
    client = _make_client()
    assert client.camera_mjpeg_url is None
```

Append to `backend/tests/services/test_elegoo_centauri.py`:

```python
def test_camera_mjpeg_url_returns_configured_url():
    client = _make_client(camera_url="http://192.168.1.20:8080/?action=stream")
    assert client.camera_mjpeg_url == "http://192.168.1.20:8080/?action=stream"


def test_camera_mjpeg_url_is_none_when_not_configured():
    client = _make_client(camera_url="")
    assert client.camera_mjpeg_url is None


def test_camera_rtsp_url_is_none_for_elegoo():
    client = _make_client()
    assert client.camera_rtsp_url is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && pytest tests/services/test_bambu_mqtt.py::test_camera_rtsp_url_returns_rtsps_url -v
```

Expected: `AttributeError: 'BambuMQTTClient' object has no attribute 'camera_rtsp_url'`

- [ ] **Step 3: Add optional properties to ABC**

In `backend/app/services/abstract_printer_client.py`, add after `file_upload_supported`:

```python
@property
def camera_mjpeg_url(self) -> str | None:
    return None

@property
def camera_rtsp_url(self) -> str | None:
    return None
```

- [ ] **Step 4: Implement on BambuMQTTClient**

In `backend/app/services/bambu_mqtt.py`, add after `get_capabilities`:

```python
@property
def camera_rtsp_url(self) -> str | None:
    return f"rtsps://bblp:{self._access_code}@{self._ip}:322/streaming/live/1"

@property
def camera_mjpeg_url(self) -> str | None:
    return None
```

- [ ] **Step 5: Implement on ElegooCentauriClient**

In `backend/app/services/elegoo_centauri_client.py`, add after `get_capabilities`:

```python
@property
def camera_mjpeg_url(self) -> str | None:
    return self._camera_url or None

@property
def camera_rtsp_url(self) -> str | None:
    return None
```

- [ ] **Step 6: Run all new tests**

```
cd backend && pytest tests/services/test_bambu_mqtt.py tests/services/test_elegoo_centauri.py -v
```

Expected: all PASS

- [ ] **Step 7: Run full suite**

```
cd backend && pytest -v
```

Expected: all PASS

- [ ] **Step 8: Commit**

```
git add backend/app/services/abstract_printer_client.py backend/app/services/bambu_mqtt.py backend/app/services/elegoo_centauri_client.py backend/tests/services/test_bambu_mqtt.py backend/tests/services/test_elegoo_centauri.py
git commit -m "feat: add camera_mjpeg_url and camera_rtsp_url properties to printer clients"
```

---

### Task 3: Camera proxy service

**Files:**
- Create: `backend/app/services/camera_proxy.py`
- Create: `backend/tests/services/test_camera_proxy.py`
- Modify: `backend/app/config.py`

Two async generator functions:
1. `stream_mjpeg(url)` — forwards MJPEG HTTP stream via httpx
2. `stream_rtsp_ffmpeg(rtsp_url)` — spawns ffmpeg, converts RTSP to MJPEG, yields stdout chunks

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/services/test_camera_proxy.py`:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_stream_mjpeg_yields_chunks():
    from app.services.camera_proxy import stream_mjpeg

    async def fake_aiter_bytes(chunk_size=None):
        yield b"chunk1"
        yield b"chunk2"

    mock_response = MagicMock()
    mock_response.aiter_bytes = fake_aiter_bytes
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream.return_value.__aenter__ = AsyncMock(return_value=mock_response)
    mock_client.stream.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.camera_proxy.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        chunks = []
        async for chunk in stream_mjpeg("http://fake/stream"):
            chunks.append(chunk)

    assert chunks == [b"chunk1", b"chunk2"]


@pytest.mark.asyncio
async def test_stream_rtsp_ffmpeg_yields_stdout():
    from app.services.camera_proxy import stream_rtsp_ffmpeg

    call_count = 0

    async def fake_read(n):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return b"ffmpeg_data"
        return b""

    mock_proc = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stdout.read = fake_read
    mock_proc.returncode = None
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("app.services.camera_proxy.asyncio.create_subprocess_exec",
               new_callable=AsyncMock, return_value=mock_proc):
        with patch("app.services.camera_proxy.get_ffmpeg_executable", return_value="ffmpeg"):
            chunks = []
            async for chunk in stream_rtsp_ffmpeg("rtsps://fake/stream"):
                chunks.append(chunk)

    assert b"ffmpeg_data" in chunks


@pytest.mark.asyncio
async def test_stream_rtsp_ffmpeg_kills_process_on_completion():
    from app.services.camera_proxy import stream_rtsp_ffmpeg

    async def fake_read(n):
        return b""

    mock_proc = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stdout.read = fake_read
    mock_proc.returncode = None
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("app.services.camera_proxy.asyncio.create_subprocess_exec",
               new_callable=AsyncMock, return_value=mock_proc):
        with patch("app.services.camera_proxy.get_ffmpeg_executable", return_value="ffmpeg"):
            async for _ in stream_rtsp_ffmpeg("rtsps://fake/stream"):
                pass

    mock_proc.kill.assert_called_once()
    mock_proc.wait.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && pytest tests/services/test_camera_proxy.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.camera_proxy'`

- [ ] **Step 3: Add `get_ffmpeg_executable` to config**

In `backend/app/config.py`, add:

```python
def get_ffmpeg_executable() -> str:
    return os.environ.get("FFMPEG_EXECUTABLE", "ffmpeg")
```

- [ ] **Step 4: Create `camera_proxy.py`**

Create `backend/app/services/camera_proxy.py`:

```python
from __future__ import annotations
import asyncio
from collections.abc import AsyncGenerator

import httpx

from ..config import get_ffmpeg_executable

CHUNK_SIZE = 8192


async def stream_mjpeg(url: str) -> AsyncGenerator[bytes, None]:
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("GET", url) as response:
            async for chunk in response.aiter_bytes(CHUNK_SIZE):
                yield chunk


async def stream_rtsp_ffmpeg(rtsp_url: str) -> AsyncGenerator[bytes, None]:
    ffmpeg = get_ffmpeg_executable()
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-f", "mpjpeg",
        "-q:v", "5",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        while True:
            chunk = await proc.stdout.read(CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
```

- [ ] **Step 5: Run the tests**

```
cd backend && pytest tests/services/test_camera_proxy.py -v
```

Expected: all 3 PASS

- [ ] **Step 6: Run full suite**

```
cd backend && pytest -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```
git add backend/app/services/camera_proxy.py backend/app/config.py backend/tests/services/test_camera_proxy.py
git commit -m "feat: add camera proxy service for MJPEG forwarding and RTSP-to-MJPEG via ffmpeg"
```

---

### Task 4: Camera route + httpx production dependency

**Files:**
- Modify: `backend/app/api/routes/printers.py`
- Modify: `backend/pyproject.toml`
- Create: `backend/tests/api/test_printers.py`

Route `GET /api/v1/printers/{id}/camera` streams camera feed. Returns 404 if printer not found or no camera capability, 503 if not connected. httpx moves from dev to production deps.

- [ ] **Step 1: Move httpx to production deps in pyproject.toml**

Edit `backend/pyproject.toml` so `httpx>=0.27.0` is in `dependencies`, not `dev`:

```toml
[project]
name = "themis"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.29.0",
    "sqlalchemy>=2.0.0",
    "aiosqlite>=0.20.0",
    "python-multipart>=0.0.9",
    "paho-mqtt>=1.6,<2.0",
    "websocket-client>=1.7",
    "httpx>=0.27.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0,<1.0",
    "pytest-mock>=3.12",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
include = ["app*"]
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/api/test_printers.py`:

```python
import pytest
from unittest.mock import MagicMock
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_camera_404_unknown_printer(client: AsyncClient):
    resp = await client.get("/api/v1/printers/999/camera")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_camera_404_no_camera_capability(client: AsyncClient):
    resp = await client.post("/api/v1/printers", json={
        "name": "NoCam",
        "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "1.2.3.4"},
    })
    assert resp.status_code == 201
    printer_id = resp.json()["id"]

    from app.services.printer_manager import printer_manager
    mock_client = MagicMock()
    mock_client.connected = True
    mock_client.get_capabilities.return_value = MagicMock(camera=False)
    mock_client.camera_mjpeg_url = None
    mock_client.camera_rtsp_url = None
    printer_manager._clients[printer_id] = mock_client

    try:
        resp = await client.get(f"/api/v1/printers/{printer_id}/camera")
        assert resp.status_code == 404
    finally:
        del printer_manager._clients[printer_id]


@pytest.mark.asyncio
async def test_camera_503_not_connected(client: AsyncClient):
    resp = await client.post("/api/v1/printers", json={
        "name": "NotConn",
        "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "1.2.3.4"},
    })
    assert resp.status_code == 201
    printer_id = resp.json()["id"]

    from app.services.printer_manager import printer_manager
    mock_client = MagicMock()
    mock_client.connected = False
    mock_client.get_capabilities.return_value = MagicMock(camera=True)
    mock_client.camera_mjpeg_url = "http://fake/stream"
    mock_client.camera_rtsp_url = None
    printer_manager._clients[printer_id] = mock_client

    try:
        resp = await client.get(f"/api/v1/printers/{printer_id}/camera")
        assert resp.status_code == 503
    finally:
        del printer_manager._clients[printer_id]
```

- [ ] **Step 3: Run tests to verify they fail**

```
cd backend && pytest tests/api/test_printers.py -k "camera" -v
```

Expected: at least the capability and connected tests fail (route not yet added).

- [ ] **Step 4: Add camera route to printers.py**

In `backend/app/api/routes/printers.py`, add to imports:

```python
from fastapi.responses import StreamingResponse
from ...services.camera_proxy import stream_mjpeg, stream_rtsp_ffmpeg
```

Add at the end of `backend/app/api/routes/printers.py`:

```python
@router.get("/{printer_id}/camera")
async def stream_camera(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    await _get_or_404(printer_id, session)
    client = printer_manager._clients.get(printer_id)
    if client is None or not client.connected:
        raise HTTPException(503, "Printer not connected")
    caps = client.get_capabilities()
    if not caps.camera:
        raise HTTPException(404, "This printer has no camera")
    if client.camera_mjpeg_url:
        stream = stream_mjpeg(client.camera_mjpeg_url)
    elif client.camera_rtsp_url:
        stream = stream_rtsp_ffmpeg(client.camera_rtsp_url)
    else:
        raise HTTPException(404, "No camera URL configured")
    return StreamingResponse(
        stream,
        media_type="multipart/x-mixed-replace; boundary=frame",
    )
```

- [ ] **Step 5: Run camera tests**

```
cd backend && pytest tests/api/test_printers.py -k "camera" -v
```

Expected: all 3 PASS

- [ ] **Step 6: Run full suite**

```
cd backend && pytest -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```
git add backend/app/api/routes/printers.py backend/pyproject.toml backend/tests/api/test_printers.py
git commit -m "feat: add camera streaming proxy route and move httpx to production deps"
```
