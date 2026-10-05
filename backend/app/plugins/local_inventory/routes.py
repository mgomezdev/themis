"""Local inventory's own routes (mounted under `/api/v1/plugins/local_inventory/`). Library CRUD is the neutral
`/api/v1/inventory/*` API (capability-gated); this router only adds what that contract has no place for."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session

router = APIRouter(tags=["local_inventory"])


@router.get("/weight-log", summary="Audit log of spool weight changes (newest first)",
            dependencies=[Depends(require_scope("inventory:read"))])
async def weight_log(spool_ref: str | None = None, limit: int = Query(default=100, ge=1, le=1000),
                     session: AsyncSession = Depends(get_session)):
    where = "WHERE spool_id = :s" if spool_ref is not None and spool_ref.isdigit() else ""
    if spool_ref is not None and not spool_ref.isdigit():
        return []
    rows = (await session.execute(text(
        f"SELECT id, spool_id, old_g, new_g, source, at FROM local_inv_weight_log {where} ORDER BY id DESC LIMIT :n"),
        {"n": limit, **({"s": int(spool_ref)} if where else {})})).mappings().all()
    return [{"id": r["id"], "spool_ref": str(r["spool_id"]), "old_g": r["old_g"], "new_g": r["new_g"],
             "source": r["source"], "at": r["at"]} for r in rows]
