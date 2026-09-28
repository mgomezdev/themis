"""Admin account operations shared by the HTTP routes and the offline CLI (app/admin.py)."""
from __future__ import annotations
import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AdminAccount, ApiKey
from .password import hash_password

logger = logging.getLogger("app.admin")

MIN_PASSWORD_LENGTH = 8
RECOVERY_MINUTES = 15
RECOVERY_MAX_ATTEMPTS = 5
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").strip().upper().encode()).hexdigest()


def validate_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH or not password.strip():
        raise ValueError(f"Admin password must be at least {MIN_PASSWORD_LENGTH} characters")


async def revoke_admin_sessions(session: AsyncSession, keep_key_id: int | None = None) -> None:
    stmt = update(ApiKey).where(ApiKey.admin_session.is_(True), ApiKey.enabled.is_(True))
    if keep_key_id is not None:
        stmt = stmt.where(ApiKey.id != keep_key_id)
    await session.execute(stmt.values(enabled=False, revoked_at=_fmt(_now())))


async def set_password(session: AsyncSession, acct: AdminAccount, password: str,
                       keep_key_id: int | None = None) -> None:
    """Set the admin password, burn any pending recovery code, sign out other admin sessions."""
    acct.password_hash = await asyncio.to_thread(hash_password, password)
    acct.recovery_code_hash = None
    acct.recovery_code_expires_at = None
    acct.recovery_attempts = 0
    await revoke_admin_sessions(session, keep_key_id)


def _code_live(acct: AdminAccount) -> bool:
    return bool(acct.recovery_code_hash and acct.recovery_code_expires_at
                and acct.recovery_code_expires_at > _fmt(_now()))


def issue_recovery_code(acct: AdminAccount) -> str | None:
    """New one-time code, or None while an unexpired one exists (so repeated requests from
    anyone can't replace the code the real admin is reading from the log)."""
    if _code_live(acct):
        return None
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(10))
    code = f"{raw[:5]}-{raw[5:]}"
    acct.recovery_code_hash = _code_hash(code)
    acct.recovery_code_expires_at = _fmt(_now() + timedelta(minutes=RECOVERY_MINUTES))
    acct.recovery_attempts = 0
    logger.warning(
        "ADMIN PASSWORD RECOVERY CODE: %s (single use, valid %d minutes). "
        "Enter it on the Themis sign-in page under 'Forgot admin password?'.",
        code, RECOVERY_MINUTES,
    )
    return code


def check_recovery_code(acct: AdminAccount, code: str) -> bool:
    """True if `code` matches the live code. Wrong guesses count; the 5th burns the code."""
    if not _code_live(acct):
        return False
    if secrets.compare_digest(acct.recovery_code_hash, _code_hash(code)):
        return True
    acct.recovery_attempts = (acct.recovery_attempts or 0) + 1
    if acct.recovery_attempts >= RECOVERY_MAX_ATTEMPTS:
        acct.recovery_code_hash = None
        acct.recovery_code_expires_at = None
    return False
