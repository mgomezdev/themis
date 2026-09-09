"""Public, unauthenticated read-only routes.

This is the ONE deliberate exception (beyond the empty-api_keys-table bootstrap
hatch) to this codebase's "every /api/v1/* route requires Depends(require_scope(...))"
invariant - see docs/agent/conventions.md § Invariants. Keep this module small and
its own file so the one auth-exempt surface in the whole API stays trivially easy to
audit in isolation. Never import require_scope here, and never add a route to this
router that isn't addressed by an unguessable per-resource token.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Job, Project
from .projects import _load_items, _load_links, _load_parts, _project_progress

router = APIRouter(prefix="/api/v1/public", tags=["public"])


@router.get("/projects/{token}", summary="Get a project via its public share token")
async def get_public_project(token: str, session: AsyncSession = Depends(get_session)) -> dict:
    proj = (await session.execute(
        select(Project).where(Project.share_token == token)
    )).scalar_one_or_none()
    if proj is None:
        raise HTTPException(404, "Not found")

    items = await _load_items(session, proj.id)
    parts = await _load_parts(session, proj.id)
    links = await _load_links(session, proj.id)
    job_rows = (await session.execute(
        select(Job).where(Job.project_id == proj.id)
    )).scalars().all()
    progress = _project_progress(job_rows)

    return {
        "name": proj.name,
        "customer": proj.customer,
        "due_date": proj.due_date,
        "on_hold": proj.on_hold,
        "items": [
            {"name": i["file_name"], "quantity": i["quantity"], "quantity_completed": i["quantity_completed"]}
            for i in items
        ],
        "parts": [{"name": p["name"], "quantity": p["quantity"]} for p in parts],
        "links": [{"url": l["url"], "label": l["label"]} for l in links],
        "jobs_total": progress["jobs_total"],
        "jobs_complete": progress["jobs_complete"],
        "estimate_seconds_remaining": progress["estimate_seconds_remaining"],
        "updated_at": proj.updated_at,
    }
