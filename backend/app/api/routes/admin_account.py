"""The single admin account: password + whether local-network devices skip sign-in."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_admin_account, require_scope
from ...database import get_session
from ...models import AdminAccount, ApiKey
from ...services import admin_account as admin_svc

router = APIRouter(prefix="/api/v1/admin-account", tags=["admin-account"])


def _admin_only(scope: str):
    """Only the admin — a local-network admin, THEMIS_BOOTSTRAP_KEY (both keyless, id None) or an
    admin login session — manages the admin account; a scoped staff/integration key can't."""
    async def _dep(key: ApiKey | None = Depends(require_scope(scope))) -> ApiKey | None:
        if key is not None and key.id is not None and not key.admin_session:
            raise HTTPException(403, "Admin sign-in required")
        return key
    return _dep


async def _full_access_keys(session: AsyncSession) -> int:
    """Enabled, unexpired API keys (not login sessions) that can manage keys — e.g. a pre-upgrade
    auto-created "Browser" key. They keep working even when sign-in is required everywhere."""
    rows = (await session.execute(select(ApiKey).where(
        ApiKey.enabled.is_(True), ApiKey.revoked_at.is_(None),
        ApiKey.admin_session.is_(False), ApiKey.customer_id.is_(None),
        or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > admin_svc._fmt(admin_svc._now())),
    ))).scalars().all()
    return sum(1 for r in rows if "apikeys:write" in (r.scopes or []))


async def _to_dict(a: AdminAccount, session: AsyncSession) -> dict:
    return {"username": a.username, "password_set": a.password_hash is not None,
            "allow_local_login": a.allow_local_login,
            "full_access_keys": await _full_access_keys(session)}


@router.get("", summary="Admin account settings")
async def get_account(session: AsyncSession = Depends(get_session),
                      _key: ApiKey | None = Depends(_admin_only("apikeys:read"))) -> dict:
    acct = await get_admin_account(session)
    await session.commit()
    return await _to_dict(acct, session)


class PasswordBody(BaseModel):
    password: str


@router.put("/password", summary="Set admin password")
async def set_password(body: PasswordBody, session: AsyncSession = Depends(get_session),
                       key: ApiKey | None = Depends(_admin_only("apikeys:write"))) -> dict:
    """Signs out every other admin session (the caller's own session, if any, stays valid)."""
    try:
        admin_svc.validate_password(body.password)
    except ValueError as e:
        raise HTTPException(422, str(e))
    acct = await get_admin_account(session)
    await admin_svc.set_password(session, acct, body.password,
                                 keep_key_id=key.id if key is not None and key.admin_session else None)
    await session.commit()
    return await _to_dict(acct, session)


class AccountPatch(BaseModel):
    allow_local_login: bool | None = None


@router.patch("", summary="Update admin account settings")
async def patch_account(body: AccountPatch, session: AsyncSession = Depends(get_session),
                        _key: ApiKey | None = Depends(_admin_only("apikeys:write"))) -> dict:
    acct = await get_admin_account(session)
    if body.allow_local_login is not None:
        if not body.allow_local_login and acct.password_hash is None:
            raise HTTPException(409, "Set an admin password before requiring sign-in on the local network")
        acct.allow_local_login = body.allow_local_login
    await session.commit()
    return await _to_dict(acct, session)
