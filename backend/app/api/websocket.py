from __future__ import annotations
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..database import get_session


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.remove(websocket)

    async def broadcast(self, event_type: str, data: Any) -> None:
        payload = {"type": event_type, "data": data}
        dead = []
        for ws in self.active_connections:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active_connections.remove(ws)


connection_manager = ConnectionManager()


async def websocket_endpoint(websocket: WebSocket, key: str | None = None) -> None:
    # Auth can't run as a normal Depends() chain for websockets, so resolve the
    # key manually before accepting — equivalent to auth.require_any_key, but
    # inlined since there's no request/response cycle to hang a dependency off.
    # Local import: avoids adding app.auth to this module's import-time surface
    # for a check that only runs once per connection.
    from ..auth import _resolve_raw_key, _table_is_empty

    session_dep = websocket.app.dependency_overrides.get(get_session, get_session)
    session_gen = session_dep()
    session = await session_gen.__anext__()
    try:
        if not await _table_is_empty(session):
            resolved = await _resolve_raw_key(key, session)
            if resolved is None:
                await websocket.close(code=4401)
                return
    finally:
        await session_gen.aclose()

    await connection_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connection_manager.disconnect(websocket)
