"""Append-only audit log for actions on the instance itself (plugin install/upgrade/uninstall, restart)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ApiKey, AuditLog


def actor_of(key: ApiKey | None) -> str:
    """Who: an admin login session (`session:<key id>`), or the keyless local admin / bootstrap key."""
    if key is not None and key.id is not None:
        return f"session:{key.id}" if key.admin_session else f"key:{key.id}"
    return "local-admin"


async def record(session: AsyncSession, actor: str, action: str, target: str | None = None, detail: dict | None = None) -> None:
    """Add (not commit) one row: it commits with the change it describes, so the log and the change cannot disagree."""
    session.add(AuditLog(at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), actor=actor, action=action,
                         target=target, detail=detail or {}))
