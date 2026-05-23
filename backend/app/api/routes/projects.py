from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Project

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None


def _to_dict(p: Project) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "created_at": p.created_at,
    }


async def _get_or_404(project_id: int, session: AsyncSession) -> Project:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return p


@router.get("")
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(select(Project))
    return [_to_dict(p) for p in result.scalars().all()]


@router.post("", status_code=201)
async def create_project(
    body: ProjectCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = Project(
        name=body.name,
        description=body.description,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return _to_dict(project)


@router.get("/{project_id}")
async def get_project(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return _to_dict(await _get_or_404(project_id, session))


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    project = await _get_or_404(project_id, session)
    await session.delete(project)
    await session.commit()
