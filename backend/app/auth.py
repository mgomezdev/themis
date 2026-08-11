from __future__ import annotations
from datetime import datetime, timezone
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_session
from .models import ApiKey
from .services.api_key_service import hash_key

SCOPES: set[str] = {
    "files:read", "files:write",
    "jobs:read", "jobs:write",
    "printers:read", "printers:write", "printers:control",
    "queue:read", "queue:write",
    "fleet:read",
    "orders:read", "orders:write",
    "projects:read", "projects:write",
    "laminus:read", "laminus:write",
    "settings:read", "settings:write",
    "spoolman:read", "spoolman:write",
    "tags:read", "tags:write",
    "maintenance:read", "maintenance:write",
    "apikeys:read", "apikeys:write",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


async def _table_is_empty(session: AsyncSession) -> bool:
    count = (await session.execute(select(func.count()).select_from(ApiKey))).scalar_one()
    return count == 0


async def _resolve_raw_key(raw: str | None, session: AsyncSession) -> ApiKey | None:
    """Look up an ApiKey from a raw key string (already extracted from wherever
    it lives — header, ?key= param, /ws query param). Shared by _resolve_key
    (HTTP request path) and the websocket auth path in api/websocket.py, which
    can't run a normal Depends() chain."""
    if not raw:
        return None
    prefix = raw[:12]
    row = (await session.execute(
        select(ApiKey).where(ApiKey.key_prefix == prefix, ApiKey.enabled == True)  # noqa: E712
    )).scalar_one_or_none()
    if row is None or row.key_hash != hash_key(raw):
        return None
    # Throttled last_used_at touch — don't write on every single request.
    if row.last_used_at is None or row.last_used_at[:16] < _now()[:16]:
        row.last_used_at = _now()
        await session.commit()
    return row


async def _resolve_key(request: Request, session: AsyncSession) -> ApiKey | None:
    raw = request.headers.get("X-Api-Key") or request.query_params.get("key")
    return await _resolve_raw_key(raw, session)


def require_scope(scope: str):
    assert scope in SCOPES, f"unknown scope {scope!r}"

    async def _dep(request: Request, session: AsyncSession = Depends(get_session)) -> ApiKey | None:
        if await _table_is_empty(session):
            return None  # bootstrap: open access until the first key is created
        key = await _resolve_key(request, session)
        if key is None:
            raise HTTPException(401, "Missing or invalid API key")
        if scope not in (key.scopes or []):
            raise HTTPException(403, f"API key lacks required scope: {scope}")
        return key

    return _dep


async def require_any_key(request: Request, session: AsyncSession = Depends(get_session)) -> ApiKey | None:
    """For /ws — any valid key, no specific scope."""
    if await _table_is_empty(session):
        return None
    key = await _resolve_key(request, session)
    if key is None:
        raise HTTPException(401, "Missing or invalid API key")
    return key
