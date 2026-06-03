from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Tag, FileTag

router = APIRouter(prefix="/api/v1/tags", tags=["tags"])


class TagCreate(BaseModel):
    name: str
    color: str = "#64748b"
    category: str = ""


class TagPatch(BaseModel):
    name: str | None = None
    color: str | None = None
    category: str | None = None


async def _usage_counts(session: AsyncSession) -> dict[int, int]:
    rows = (await session.execute(
        select(FileTag.tag_id, func.count()).group_by(FileTag.tag_id)
    )).all()
    return {tag_id: n for tag_id, n in rows}


def _to_dict(t: Tag, usage: int) -> dict:
    return {"id": t.id, "name": t.name, "color": t.color,
            "category": t.category, "usage_count": usage}


@router.get("")
async def list_tags(session: AsyncSession = Depends(get_session)) -> list[dict]:
    usage = await _usage_counts(session)
    tags = (await session.execute(select(Tag).order_by(Tag.category, Tag.name))).scalars().all()
    return [_to_dict(t, usage.get(t.id, 0)) for t in tags]


@router.post("", status_code=201)
async def create_tag(body: TagCreate, session: AsyncSession = Depends(get_session)) -> dict:
    existing = (await session.execute(select(Tag).where(Tag.name == body.name))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, f"Tag {body.name!r} already exists")
    tag = Tag(name=body.name, color=body.color, category=body.category,
              created_at=datetime.now(timezone.utc).isoformat())
    session.add(tag)
    await session.commit()
    await session.refresh(tag)
    return _to_dict(tag, 0)


@router.patch("/{tag_id}")
async def update_tag(tag_id: int, body: TagPatch, session: AsyncSession = Depends(get_session)) -> dict:
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(404, f"Tag {tag_id} not found")
    if body.name is not None and body.name != tag.name:
        dup = (await session.execute(select(Tag).where(Tag.name == body.name))).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(409, f"Tag {body.name!r} already exists")
        tag.name = body.name
    if body.color is not None:
        tag.color = body.color
    if body.category is not None:
        tag.category = body.category
    await session.commit()
    usage = (await _usage_counts(session)).get(tag.id, 0)
    return _to_dict(tag, usage)


@router.delete("/{tag_id}")
async def delete_tag(tag_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(404, f"Tag {tag_id} not found")
    # Explicit cascade (SQLite FK cascade is not enforced by default here).
    for link in (await session.execute(select(FileTag).where(FileTag.tag_id == tag_id))).scalars().all():
        await session.delete(link)
    await session.delete(tag)
    await session.commit()
    return {"deleted": tag_id}
