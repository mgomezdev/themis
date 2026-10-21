"""The job-detail field catalog: which enabled plugins declare which fields (docs/printer-interface.md)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...auth import require_scope
from ...services import job_details

router = APIRouter(prefix="/api/v1/job-fields", tags=["jobs"])


@router.get("", summary="List job detail fields", dependencies=[Depends(require_scope("jobs:read"))])
async def list_job_fields() -> list[dict]:
    """Every field an enabled plugin declares (or lists as unsupported), with its sources. Field names are not enforced: a plugin may
    also write fields nobody declared; those answer `unknown_field` only when nothing is stored."""
    return [{"name": name, "sources": sources} for name, sources in sorted(job_details.field_catalog().items())]
