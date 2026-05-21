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
