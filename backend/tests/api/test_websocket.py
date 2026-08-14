import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.api.websocket import connection_manager


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

    mgr.active_connections.append(ws)
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
    mgr.active_connections.append(dead_ws)

    await asyncio.gather(
        mgr.broadcast("printer_state", {}),
        mgr.broadcast("printer_state", {}),
    )
    assert mgr.active_connections == []
