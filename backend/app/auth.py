from __future__ import annotations
import ipaddress
import os
import secrets
from datetime import datetime, timezone
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_session
from .models import AdminAccount, ApiKey
from .services.api_key_service import hash_key

SCOPES: set[str] = {
    "files:read", "files:write",
    "jobs:read", "jobs:write",  # jobs:write includes ability to stop printers (via job cancel)
    "printers:read", "printers:write", "printers:control",
    "queue:read", "queue:write",
    "fleet:read",
    "orders:read", "orders:write",
    "projects:read", "projects:write", "projects:share",
    "laminus:read", "laminus:write",
    "settings:read", "settings:write",
    "spoolman:read", "spoolman:write",
    "tags:read", "tags:write",
    "maintenance:read", "maintenance:write",
    "apikeys:read", "apikeys:write",
    "customers:read", "customers:write",
    "customer",  # customer login sessions: only the /api/v1/customer/* portal
}

_DEFAULT_LOCAL_NETWORKS = "192.168.0.0/16"


def is_local(host: str | None) -> bool:
    """True when the client IP falls in THEMIS_LOCAL_NETWORKS (comma-separated CIDRs).
    Local clients get full admin with no key. Only the socket peer address is trusted —
    never proxy headers — so a reverse proxy's (or Docker NAT gateway's) own IP must not be inside
    this range. A valid presented key always takes precedence over local mode."""
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    for cidr in os.environ.get("THEMIS_LOCAL_NETWORKS", _DEFAULT_LOCAL_NETWORKS).split(","):
        cidr = cidr.strip()
        if not cidr:
            continue
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def local_admin_key() -> ApiKey:
    return ApiKey(id=None, key_prefix=None, key_hash=None, scopes=sorted(SCOPES),
                  enabled=True, revoked_at=None, expires_at=None, last_used_at=None)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


async def get_admin_account(session: AsyncSession) -> AdminAccount:
    """The admin singleton. Migration v022 creates it on first boot; created here too if missing
    (tests build the schema with create_all, not migrations)."""
    acct = await session.get(AdminAccount, 1)
    if acct is None:
        acct = AdminAccount(id=1, username="admin", allow_local_login=True, recovery_attempts=0)
        session.add(acct)
        await session.flush()
    return acct


async def local_admin_allowed(host: str | None, session: AsyncSession) -> bool:
    """Keyless local-network access is admin only while the admin account allows it."""
    return is_local(host) and (await get_admin_account(session)).allow_local_login


async def _resolve_raw_key(raw: str | None, session: AsyncSession) -> ApiKey | None:
    """Look up an ApiKey from a raw key string (already extracted from wherever
    it lives — header, ?key= param, /ws query param). Shared by _resolve_key
    (HTTP request path) and the websocket auth path in api/websocket.py, which
    can't run a normal Depends() chain."""
    if not raw:
        return None
    bootstrap_key = os.environ.get("THEMIS_BOOTSTRAP_KEY")
    if bootstrap_key and secrets.compare_digest(raw, bootstrap_key):
        return ApiKey(id=None, key_prefix=None, key_hash=None, scopes=sorted(SCOPES),
                      enabled=True, revoked_at=None, expires_at=None, last_used_at=None)
    prefix = raw[:12]
    row = (await session.execute(
        select(ApiKey).where(
            ApiKey.key_prefix == prefix,
            ApiKey.enabled == True,  # noqa: E712
            ApiKey.revoked_at.is_(None),
            or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > _now()),
        )
    )).scalar_one_or_none()
    if row is None or not secrets.compare_digest(row.key_hash, hash_key(raw)):
        return None
    # Throttled last_used_at touch — don't write on every single request.
    if row.last_used_at is None or row.last_used_at[:16] < _now()[:16]:
        row.last_used_at = _now()
        await session.commit()
    return row


def _path_allows_query_param(path: str) -> bool:
    """Check if a path is allowed to use ?key= query param for auth."""
    return "/thumbnails/" in path or path.endswith(("/snapshot", "/camera"))   # <img src> can't send headers


async def _resolve_key(request: Request, session: AsyncSession) -> ApiKey | None:
    raw = request.headers.get("X-Api-Key")
    if raw is None and _path_allows_query_param(request.url.path):
        raw = request.query_params.get("key")
    return await _resolve_raw_key(raw, session)


def require_scope(scope: str):
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")

    async def _dep(request: Request, session: AsyncSession = Depends(get_session)) -> ApiKey | None:
        # A presented key wins over local mode, so a customer signed in on the LAN stays a customer.
        key = await _resolve_key(request, session)
        if key is None:
            if await local_admin_allowed(request.client.host if request.client else None, session):
                return local_admin_key()
            raise HTTPException(401, "Missing or invalid API key")
        if scope not in (key.scopes or []):
            raise HTTPException(403, f"API key lacks required scope: {scope}")
        return key

    return _dep


async def require_any_key(request: Request, session: AsyncSession = Depends(get_session)) -> ApiKey | None:
    """For /ws — any valid key, no specific scope."""
    key = await _resolve_key(request, session)
    if key is not None:
        return key
    if await local_admin_allowed(request.client.host if request.client else None, session):
        return local_admin_key()
    raise HTTPException(401, "Missing or invalid API key")


async def require_customer(key: ApiKey | None = Depends(require_scope("customer"))) -> int:
    """Customer portal routes: returns the logged-in customer's id. Staff/local/integration
    keys carry no customer_id and are refused — the portal is always scoped to one customer."""
    if key is None or key.customer_id is None:
        raise HTTPException(403, "Customer account required")
    return key.customer_id
