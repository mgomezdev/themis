"""The single admin account: password + whether local-network devices skip sign-in."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_admin_account, require_scope
from ...database import get_session
from ...models import AdminAccount, ApiKey
from ...services import admin_account as admin_svc

router = APIRouter(prefix="/api/v1/admin-account", tags=["admin-account"])


def _to_dict(a: AdminAccount) -> dict:
    return {"username": a.username, "password_set": a.password_hash is not None,
            "allow_local_login": a.allow_local_login}


@router.get("", summary="Admin account settings", dependencies=[Depends(require_scope("apikeys:read"))])
async def get_account(session: AsyncSession = Depends(get_session)) -> dict:
    acct = await get_admin_account(session)
    await session.commit()
    return _to_dict(acct)


class PasswordBody(BaseModel):
    password: str


@router.put("/password", summary="Set admin password")
async def set_password(body: PasswordBody, session: AsyncSession = Depends(get_session),
                       key: ApiKey | None = Depends(require_scope("apikeys:write"))) -> dict:
    """Signs out every other admin session (the caller's own session, if any, stays valid)."""
    if not body.password:
        raise HTTPException(422, "password is required")
    acct = await get_admin_account(session)
    await admin_svc.set_password(session, acct, body.password,
                                 keep_key_id=key.id if key is not None and key.admin_session else None)
    await session.commit()
    return _to_dict(acct)


class AccountPatch(BaseModel):
    allow_local_login: bool | None = None


@router.patch("", summary="Update admin account settings",
              dependencies=[Depends(require_scope("apikeys:write"))])
async def patch_account(body: AccountPatch, session: AsyncSession = Depends(get_session)) -> dict:
    acct = await get_admin_account(session)
    if body.allow_local_login is not None:
        if not body.allow_local_login and acct.password_hash is None:
            raise HTTPException(409, "Set an admin password before requiring sign-in on the local network")
        acct.allow_local_login = body.allow_local_login
    await session.commit()
    return _to_dict(acct)
