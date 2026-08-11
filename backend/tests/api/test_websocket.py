import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.database import Base, get_session
from app.api.websocket import connection_manager
from app.models import ApiKey
from app.services.api_key_service import generate_key, hash_key


def test_connection_manager_starts_empty():
    mgr = connection_manager
    assert isinstance(mgr.active_connections, list)


@pytest.mark.asyncio
async def test_broadcast_sends_to_connections():
    from unittest.mock import AsyncMock
    from app.api.websocket import ConnectionManager
    mgr = ConnectionManager()
    mock_ws = AsyncMock()
    mgr.active_connections.append(mock_ws)
    await mgr.broadcast("printer_state", {"id": 1, "state": "IDLE"})
    mock_ws.send_json.assert_called_once()
    call_args = mock_ws.send_json.call_args[0][0]
    assert call_args["type"] == "printer_state"
    assert call_args["data"]["id"] == 1


# --- /ws auth (Task 6) -------------------------------------------------------
#
# These use a *file-backed* sqlite db (not ":memory:") so that a separate
# throwaway event loop can populate the schema/seed data and cleanly dispose
# its engine before the TestClient's own (different) event loop opens a fresh
# connection to the same file — sharing one aiosqlite connection object across
# event loops isn't safe, but sharing a sqlite *file* sequentially is.

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _seed_db(db_path: Path, scopes: list[str] | None) -> str | None:
    async def _run() -> str | None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        raw = None
        if scopes is not None:
            raw, prefix = generate_key()
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as s:
                s.add(ApiKey(
                    name="t", key_prefix=prefix, key_hash=hash_key(raw),
                    scopes=scopes, enabled=True, created_at=_now(),
                ))
                await s.commit()
        await engine.dispose()
        return raw

    return asyncio.run(_run())


def _wire_app_to_db(db_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_session():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()
    connection_manager.active_connections.clear()


def test_ws_closes_4401_without_key_when_a_key_exists(tmp_path):
    db_path = tmp_path / "ws1.db"
    raw = _seed_db(db_path, scopes=["fleet:read"])
    assert raw
    _wire_app_to_db(db_path)

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws"):
            pass
    assert exc_info.value.code == 4401


def test_ws_accepts_with_valid_key(tmp_path):
    db_path = tmp_path / "ws2.db"
    raw = _seed_db(db_path, scopes=["fleet:read"])
    assert raw
    _wire_app_to_db(db_path)

    client = TestClient(app)
    with client.websocket_connect(f"/ws?key={raw}") as ws:
        assert len(connection_manager.active_connections) == 1


def test_ws_accepts_when_table_empty(tmp_path):
    db_path = tmp_path / "ws3.db"
    _seed_db(db_path, scopes=None)  # schema only, no keys — bootstrap window
    _wire_app_to_db(db_path)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        assert len(connection_manager.active_connections) == 1


def test_ws_closes_4401_with_bad_key(tmp_path):
    db_path = tmp_path / "ws4.db"
    raw = _seed_db(db_path, scopes=["fleet:read"])
    assert raw
    _wire_app_to_db(db_path)

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws?key=thm_not-a-real-key"):
            pass
    assert exc_info.value.code == 4401
