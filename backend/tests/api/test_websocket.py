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
    mgr.active_connections.append((mock_ws, ["fleet:read"]))
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


def test_disconnect_is_idempotent_on_already_reaped_socket():
    """disconnect() must not raise when the socket was already removed — e.g. a
    broadcast reaped it as dead before the endpoint's WebSocketDisconnect handler
    got around to calling disconnect() for the same socket."""
    from unittest.mock import MagicMock
    from app.api.websocket import ConnectionManager
    mgr = ConnectionManager()
    ws = MagicMock()
    mgr.disconnect(ws)  # never connected — must not raise
    assert mgr.active_connections == []

    mgr.active_connections.append((ws, []))
    mgr.disconnect(ws)
    mgr.disconnect(ws)  # second call on the same socket — must not raise
    assert mgr.active_connections == []


@pytest.mark.asyncio
async def test_broadcast_reap_does_not_raise_when_two_broadcasts_race():
    """Two concurrent broadcasts can both observe the same socket as dead before
    either removes it; both then try to remove it from active_connections —
    must not raise ValueError."""
    import asyncio
    from unittest.mock import AsyncMock
    from app.api.websocket import ConnectionManager
    mgr = ConnectionManager()
    dead_ws = AsyncMock()

    async def failing_send(payload):
        # A real suspension point so both gathered broadcasts get past send_json
        # (and each collect dead_ws into their own local `dead` list) before
        # either proceeds to remove it — reproducing the real interleave.
        await asyncio.sleep(0)
        raise RuntimeError("connection closed")

    dead_ws.send_json = failing_send
    mgr.active_connections.append((dead_ws, ["fleet:read"]))

    await asyncio.gather(
        mgr.broadcast("printer_state", {}),
        mgr.broadcast("printer_state", {}),
    )
    assert mgr.active_connections == []


def test_ws_narrow_scope_key_receives_only_in_scope_events(tmp_path):
    """Test that /ws enforces scope checking — a key with only fleet:read
    receives fleet-scoped events but NOT job or queue events."""
    db_path = tmp_path / "ws_scope_narrow.db"
    # Create key with only fleet:read scope (narrow, not a catch-all)
    raw = _seed_db(db_path, scopes=["fleet:read"])
    assert raw
    _wire_app_to_db(db_path)

    client = TestClient(app)
    with client.websocket_connect(f"/ws?key={raw}") as ws:
        assert len(connection_manager.active_connections) == 1

        # Broadcast job_update (requires jobs:read) — should be filtered out
        asyncio.run(connection_manager.broadcast(
            "job_update",
            {"id": 1, "status": "PRINTING"}
        ))

        # Broadcast printer_state (requires fleet:read) — should be received
        asyncio.run(connection_manager.broadcast(
            "printer_state",
            {"id": 2, "state": "IDLE"}
        ))

        # Only printer_state should arrive
        data = ws.receive_json()
        assert data["type"] == "printer_state"
        assert data["data"]["id"] == 2


def test_ws_queue_scope_key_receives_queue_updates(tmp_path):
    """Test that a key with queue:read receives queue_update events."""
    db_path = tmp_path / "ws_scope_queue.db"
    raw = _seed_db(db_path, scopes=["queue:read"])
    assert raw
    _wire_app_to_db(db_path)

    client = TestClient(app)
    with client.websocket_connect(f"/ws?key={raw}") as ws:
        assert len(connection_manager.active_connections) == 1

        # Broadcast queue_update (requires queue:read) — should be received
        asyncio.run(connection_manager.broadcast(
            "queue_update",
            {"queue_length": 3}
        ))

        data = ws.receive_json()
        assert data["type"] == "queue_update"
        assert data["data"]["queue_length"] == 3


def test_ws_closes_4403_with_insufficient_scopes(tmp_path):
    """Test that /ws rejects at connect-time any key lacking all three required
    scopes (fleet:read, jobs:read, queue:read). A key with only apikeys:read
    should be rejected with close code 4403."""
    db_path = tmp_path / "ws_scope_insufficient.db"
    raw = _seed_db(db_path, scopes=["apikeys:read"])
    assert raw
    _wire_app_to_db(db_path)

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws?key={raw}"):
            pass
    assert exc_info.value.code == 4403
