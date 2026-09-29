"""Login + session introspection. Deliberately unauthenticated (like public.py): these are
the routes a browser calls *before* it has a credential. Keep this file limited to that."""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import SCOPES, _resolve_key, get_admin_account, local_admin_allowed
from ...database import get_session
from ...models import ApiKey, Customer
from ...services.api_key_service import generate_key, hash_key
from ...services import admin_account as admin_svc
from ...services import login_throttle
from ...services.password import hash_password, verify_password

_DUMMY_HASH = hash_password("")

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_SESSION_DAYS = 30


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


class LoginBody(BaseModel):
    email: str  # customer email, or the admin username ("admin")
    password: str


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _admin_login(body: LoginBody, session: AsyncSession) -> dict:
    acct = await get_admin_account(session)
    stored = acct.password_hash or _DUMMY_HASH
    ok = await asyncio.to_thread(verify_password, body.password, stored)
    if not acct.password_hash or not ok:
        raise HTTPException(401, "Invalid email or password")
    now = datetime.now(timezone.utc)
    raw, prefix = generate_key()
    session.add(ApiKey(
        name="Admin session", key_prefix=prefix, key_hash=hash_key(raw),
        scopes=sorted(SCOPES - {"customer"}), enabled=True, created_at=_fmt(now),
        expires_at=_fmt(now + timedelta(days=_SESSION_DAYS)), admin_session=True,
    ))
    await session.commit()
    return {"key": raw, "customer": None, "admin": True}


@router.post("/login", summary="Customer or admin login")
async def login(body: LoginBody, request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    """Exchange email (or the admin username) + password for a session key (sent as X-Api-Key).
    Failed attempts are throttled per client IP (services/login_throttle.py)."""
    login_throttle.check(_ip(request))
    try:
        return await _login(body, session)
    except HTTPException as e:
        if e.status_code == 401:
            login_throttle.record_failure(_ip(request))
        raise


async def _login(body: LoginBody, session: AsyncSession) -> dict:
    if body.email.strip().lower() == (await get_admin_account(session)).username.lower():
        return await _admin_login(body, session)
    cust = (await session.execute(
        select(Customer).where(func.lower(Customer.email) == body.email.strip().lower())
    )).scalar_one_or_none()
    # PBKDF2 is deliberately slow: run it off the event loop, and hash even for an unknown
    # email so response timing doesn't reveal which accounts exist.
    # A customer with no password set (empty hash) can't sign in; still hash for equal timing.
    stored = cust.password_hash if cust is not None and cust.password_hash else _DUMMY_HASH
    ok = await asyncio.to_thread(verify_password, body.password, stored)
    if cust is None or not cust.password_hash or not cust.enabled or not ok:
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
    """`role`: "admin" (local network or admin session), "staff" (API key), "customer", or null
    when the request carries no valid credential."""
    key = await _resolve_key(request, session)
    if key is None:
        if await local_admin_allowed(request.client.host if request.client else None, session):
            await session.commit()  # persists the admin row if this created it
            return {"local": True, "role": "admin", "customer": None}
        return {"local": False, "role": None, "customer": None}
    if key.admin_session:
        return {"local": False, "role": "admin", "customer": None}
    if key.customer_id is None:
        return {"local": False, "role": "staff", "customer": None}
    cust = await session.get(Customer, key.customer_id)
    return {
        "local": False, "role": "customer",
        "customer": {"id": cust.id, "name": cust.name, "email": cust.email} if cust else None,
    }


# ---- Offline admin password recovery ---------------------------------------------------
# A one-time code goes to the server log; whoever can read the log (the host operator) can
# reset the admin password. No network/email needed — works on an isolated install.

@router.post("/recover", status_code=202, summary="Write an admin recovery code to the server log")
async def recover(session: AsyncSession = Depends(get_session)) -> dict:
    acct = await get_admin_account(session)
    admin_svc.issue_recovery_code(acct)
    await session.commit()
    return {"detail": "If recovery is available, a one-time code is in the server log "
                      f"(valid {admin_svc.RECOVERY_MINUTES} minutes)."}


class RecoverConfirm(BaseModel):
    code: str
    password: str


@router.post("/recover/confirm", summary="Set the admin password with a recovery code")
async def recover_confirm(body: RecoverConfirm, request: Request,
                          session: AsyncSession = Depends(get_session)) -> dict:
    login_throttle.check(_ip(request))
    try:
        admin_svc.validate_password(body.password)
    except ValueError as e:
        raise HTTPException(422, str(e))
    acct = await get_admin_account(session)
    ok = admin_svc.check_recovery_code(acct, body.code)
    if not ok:
        await session.commit()  # persist the attempt count
        login_throttle.record_failure(_ip(request))
        raise HTTPException(400, "Invalid or expired recovery code")
    await admin_svc.set_password(session, acct, body.password)
    await session.commit()
    return {"detail": "Admin password set. Sign in as \"admin\"."}
