from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import Project, ProjectLabor

router = APIRouter(prefix="/api/v1/projects/{project_id}/labor", tags=["labor"])


class LaborCreate(BaseModel):
    minutes: int = Field(gt=0, le=60 * 24 * 31)
    logged_on: Optional[date] = None   # default: today (UTC)
    note: Optional[str] = Field(default=None, max_length=500)


def _dict(l: ProjectLabor) -> dict:
    return {"id": l.id, "project_id": l.project_id, "minutes": l.minutes, "logged_on": l.logged_on,
            "note": l.note, "created_at": l.created_at}


async def _project_or_404(project_id: int, session: AsyncSession) -> Project:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "Project not found")
    return p


@router.get("", summary="List logged labour (newest first)", responses={404: {"description": "Project not found"}},
            dependencies=[Depends(require_scope("projects:read"))])
async def list_labor(project_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    await _project_or_404(project_id, session)
    rows = (await session.execute(
        select(ProjectLabor).where(ProjectLabor.project_id == project_id)
        .order_by(ProjectLabor.logged_on.desc(), ProjectLabor.id.desc())
    )).scalars().all()
    return [_dict(r) for r in rows]


@router.post("", status_code=201, summary="Log labour time",
             responses={404: {"description": "Project not found"}, 422: {"description": "Invalid entry"}},
             dependencies=[Depends(require_scope("projects:write"))])
async def add_labor(project_id: int, body: LaborCreate, session: AsyncSession = Depends(get_session)) -> dict:
    """Setup, post-processing, assembly, packing… priced at the shop labour rate (Settings → Costs)."""
    await _project_or_404(project_id, session)
    today = datetime.now(timezone.utc).date()
    day = body.logged_on or today
    if day > today + timedelta(days=1):   # a day of slack for time zones ahead of UTC
        raise HTTPException(422, "logged_on can't be in the future")
    row = ProjectLabor(project_id=project_id, minutes=body.minutes, logged_on=day.isoformat(),
                       note=(body.note or "").strip() or None, created_at=datetime.now(timezone.utc).isoformat())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _dict(row)


@router.delete("/{labor_id}", summary="Delete a labour entry",
               responses={404: {"description": "Project or entry not found"}},
               dependencies=[Depends(require_scope("projects:write"))])
async def delete_labor(project_id: int, labor_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    await _project_or_404(project_id, session)
    row = await session.get(ProjectLabor, labor_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(404, f"Labour entry {labor_id} not found in project {project_id}")
    await session.delete(row)
    await session.commit()
    return {"deleted": labor_id}
