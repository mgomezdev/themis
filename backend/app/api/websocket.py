from __future__ import annotations
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..database import get_session

logger = logging.getLogger("app.websocket")

# Map each broadcast event type to its required scope(s).
# Connections are filtered per-event: if a connection lacks the required scope,
# it doesn't receive the event. New event types should be added here.
EVENT_SCOPE_MAP = {
    "printer_state": "fleet:read",
    "plate_clear_required": "fleet:read",
    "job_updated": "jobs:read",
    "job_update": "jobs:read",
    "queue_update": "queue:read",
}


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[tuple[WebSocket, list[str]]] = []

    async def connect(self, websocket: WebSocket, scopes: list[str]) -> None:
        await websocket.accept()
        self.active_connections.append((websocket, scopes))

    def disconnect(self, websocket: WebSocket) -> None:
        # Idempotent: a broadcast may have already reaped this socket (or another
        # concurrent broadcast/disconnect may reap it between the membership check
        # and removal below on other callers), so don't raise if it's already gone.
        self.active_connections = [
            (ws, scopes) for ws, scopes in self.active_connections if ws != websocket
        ]

    async def broadcast(self, event_type: str, data: Any) -> None:
        payload = {"type": event_type, "data": data}
        required_scope = EVENT_SCOPE_MAP.get(event_type)
        dead = []
        for ws, scopes in self.active_connections:
            # Filter by required scope: if the event has a mapped scope, only send to
            # connections that have it. Unmapped events are sent to all (fail-open).
            if required_scope and required_scope not in scopes:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active_connections = [
                (c_ws, c_scopes) for c_ws, c_scopes in self.active_connections if c_ws != ws
            ]


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
        scopes = None
        if not await _table_is_empty(session):
            resolved = await _resolve_raw_key(key, session)
            if resolved is None:
                await websocket.close(code=4401)
                return
            # Check that the key has at least one of the required fleet/jobs/queue scopes.
            required = {"fleet:read", "jobs:read", "queue:read"}
            if not (required & set(resolved.scopes or [])):
                await websocket.close(code=4403)
                return
            scopes = resolved.scopes
        else:
            # Bootstrap: grant all three scopes so filtering logic is uniform.
            scopes = ["fleet:read", "jobs:read", "queue:read"]
    finally:
        await session_gen.aclose()

    await connection_manager.connect(websocket, scopes)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connection_manager.disconnect(websocket)
    except Exception:
        logger.exception("Unexpected error in websocket_endpoint")
        connection_manager.disconnect(websocket)
