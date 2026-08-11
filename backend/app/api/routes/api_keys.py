from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import SCOPES, require_scope, _table_is_empty
from ...database import get_session
from ...models import ApiKey
from ...services.api_key_service import generate_key, hash_key

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _to_dict(row: ApiKey) -> dict:
    return {
        "id": row.id, "name": row.name, "key_prefix": row.key_prefix,
        "scopes": row.scopes or [], "enabled": row.enabled,
        "created_at": row.created_at, "last_used_at": row.last_used_at,
        "revoked_at": row.revoked_at,
    }


async def _get_or_404(session: AsyncSession, key_id: int) -> ApiKey:
    row = await session.get(ApiKey, key_id)
    if row is None:
        raise HTTPException(404, "API key not found")
    return row


async def _enabled_apikeys_write_count(session: AsyncSession, exclude_id: int | None = None) -> int:
    rows = (await session.execute(select(ApiKey).where(ApiKey.enabled == True))).scalars().all()  # noqa: E712
    return sum(1 for r in rows if r.id != exclude_id and "apikeys:write" in (r.scopes or []))


class ApiKeyCreate(BaseModel):
    name: str
    scopes: list[str] = []


@router.get("", dependencies=[Depends(require_scope("apikeys:read"))])
async def list_keys(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))).scalars().all()
    return [_to_dict(r) for r in rows]


@router.post("", dependencies=[Depends(require_scope("apikeys:write"))])
async def create_key(body: ApiKeyCreate, session: AsyncSession = Depends(get_session)):
    bootstrap = await _table_is_empty(session)
    scopes = sorted(SCOPES) if bootstrap else body.scopes
    unknown = set(scopes) - SCOPES
    if unknown:
        raise HTTPException(422, f"Unknown scope(s): {', '.join(sorted(unknown))}")

    raw, prefix = generate_key()
    row = ApiKey(
        name=body.name, key_prefix=prefix, key_hash=hash_key(raw),
        scopes=scopes, enabled=True, created_at=_now(),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {**_to_dict(row), "key": raw}  # raw key: this response only, ever


@router.post("/{key_id}/revoke", dependencies=[Depends(require_scope("apikeys:write"))])
async def revoke_key(key_id: int, session: AsyncSession = Depends(get_session)):
    row = await _get_or_404(session, key_id)
    if "apikeys:write" in (row.scopes or []) and await _enabled_apikeys_write_count(session, exclude_id=key_id) == 0:
        raise HTTPException(400, "Cannot revoke the last key with API-key management access")
    row.enabled = False
    row.revoked_at = _now()
    await session.commit()
    return _to_dict(row)


@router.delete("/{key_id}", dependencies=[Depends(require_scope("apikeys:write"))])
async def delete_key(key_id: int, session: AsyncSession = Depends(get_session)):
    row = await _get_or_404(session, key_id)
    if row.enabled and "apikeys:write" in (row.scopes or []) and await _enabled_apikeys_write_count(session, exclude_id=key_id) == 0:
        raise HTTPException(400, "Cannot delete the last key with API-key management access")
    await session.delete(row)
    await session.commit()
    return {"ok": True}
