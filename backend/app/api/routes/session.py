"""Login + session introspection. Deliberately unauthenticated (like public.py): these are
the routes a browser calls *before* it has a credential. Keep this file limited to that."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import _resolve_key, _table_is_empty, is_local
from ...database import get_session
from ...models import ApiKey, Customer
from ...services.api_key_service import generate_key, hash_key
from ...services.password import verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_SESSION_DAYS = 30


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/login", summary="Customer login")
async def login(body: LoginBody, session: AsyncSession = Depends(get_session)) -> dict:
    """Exchange customer email + password for a session key (sent as X-Api-Key afterwards)."""
    cust = (await session.execute(
        select(Customer).where(func.lower(Customer.email) == body.email.strip().lower())
    )).scalar_one_or_none()
    if cust is None or not cust.enabled or not verify_password(body.password, cust.password_hash):
        raise HTTPException(401, "Invalid email or password")

    now = datetime.now(timezone.utc)
    raw, prefix = generate_key()
    session.add(ApiKey(
        name=f"Customer session: {cust.email}", key_prefix=prefix, key_hash=hash_key(raw),
        scopes=["customer"], enabled=True, created_at=_fmt(now),
        expires_at=_fmt(now + timedelta(days=_SESSION_DAYS)), customer_id=cust.id,
    ))
    await session.commit()
    return {"key": raw, "customer": {"id": cust.id, "name": cust.name, "email": cust.email}}


@router.get("/me", summary="Current session")
async def me(request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    """`role`: "admin" (local network or bootstrap), "staff" (API key), "customer", or null
    when the request carries no valid credential."""
    if is_local(request.client.host if request.client else None):
        return {"local": True, "role": "admin", "customer": None}
    if await _table_is_empty(session):
        return {"local": False, "role": "admin", "customer": None}
    key = await _resolve_key(request, session)
    if key is None:
        return {"local": False, "role": None, "customer": None}
    if key.customer_id is None:
        return {"local": False, "role": "staff", "customer": None}
    cust = await session.get(Customer, key.customer_id)
    return {
        "local": False, "role": "customer",
        "customer": {"id": cust.id, "name": cust.name, "email": cust.email} if cust else None,
    }
