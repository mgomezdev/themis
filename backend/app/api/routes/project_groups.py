"""Print groups of a project (BIZ-360): list, create, configure (name, envelope, colour rule, eligibility targets), reorder, delete."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from math import isfinite
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import Project, ProjectGroup, ProjectGroupTarget, ProjectItem
from ...services import project_groups as pg
from ...services.providers.slicing import get_format_provider

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class GroupTargetIn(BaseModel):
    target_key: str = Field(min_length=1, max_length=64)    # "<printer id>" | "model:<model uuid>" | "any"
    process_profile: str = ""                               # "" = the printer's own default process profile


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class GroupPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    envelope_x: Optional[float] = Field(default=None, gt=0)     # explicit null (both) = auto
    envelope_y: Optional[float] = Field(default=None, gt=0)
    mix_colours: Optional[bool] = None
    targets: Optional[list[GroupTargetIn]] = None               # full replace, in order
    strategy: Optional[Literal["fixed", "pool"]] = None         # BIZ-362: pack now for one bed, or when a printer picks it up
    max_parts: Optional[int] = Field(default=None, ge=1, le=500)   # pool: parts per claimed plate cap (null = as many as fit)
    overrides: Optional[dict] = Field(default=None, description="Curated process overrides copied to future jobs. Null/{} clears; omission preserves. Not for pre-sliced G-code.")

    @field_validator("overrides")
    @classmethod
    def valid_overrides(cls, value):
        if not value:
            return None
        allowed = {setting.key for setting in get_format_provider().curated_settings()}
        if not set(value) <= allowed:
            raise ValueError("Unknown process override key")
        if any(not isinstance(v, (str, int, float, bool)) or (isinstance(v, float) and not isfinite(v)) for v in value.values()):
            raise ValueError("Process overrides must be scalar finite values")
        return {key: ("1" if v else "0") if isinstance(v, bool) else str(v) for key, v in value.items()}


class GroupOrder(BaseModel):
    group_ids: list[int]


def _dict(g: ProjectGroup, targets: list[ProjectGroupTarget]) -> dict:
    return {
        "id": g.id, "project_id": g.project_id, "name": g.name, "sort_order": g.sort_order, "is_default": bool(g.is_default),
        "strategy": g.strategy, "envelope_x": g.envelope_x, "envelope_y": g.envelope_y, "mix_colours": bool(g.mix_colours),
        "targets": [{"target_key": t.target_key, "process_profile": t.process_profile} for t in targets],
        "max_parts": g.max_parts,
        "overrides": g.overrides,
    }


async def _full(session: AsyncSession, g: ProjectGroup) -> dict:
    """The group plus, for a pool, its done / printing / left counts and why it is stuck (BIZ-362)."""
    out = _dict(g, await pg.targets_of(session, g.id))
    if g.strategy == "pool":
        from ...services import pools
        out["pool"] = await pools.stats(session, g)
        out["pool_warning"] = await pools.warning(session, g) if g.queue_position is not None else None
    else:
        out["pool"] = None
        out["pool_warning"] = None
    return out


async def _project(session: AsyncSession, project_id: int) -> Project:
    proj = await session.get(Project, project_id)
    if proj is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return proj


async def _group(session: AsyncSession, project_id: int, group_id: int) -> ProjectGroup:
    g = await session.get(ProjectGroup, group_id)
    if g is None or g.project_id != project_id:
        raise HTTPException(404, f"Group {group_id} not found in project {project_id}")
    return g


@router.get("/{project_id}/groups", summary="List a project's print groups, in queue order",
            dependencies=[Depends(require_scope("projects:read"))])
async def list_groups(project_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    await _project(session, project_id)
    groups = await pg.groups_of(session, project_id)
    await session.commit()                                   # a lazily created Default Group is kept
    return [await _full(session, g) for g in groups]


@router.post("/{project_id}/groups", status_code=201, summary="Add a print group (last in queue order)",
             dependencies=[Depends(require_scope("projects:write"))])
async def create_group(project_id: int, body: GroupCreate, session: AsyncSession = Depends(get_session)) -> dict:
    await _project(session, project_id)
    await pg.ensure_default_group(session, project_id)
    g = ProjectGroup(project_id=project_id, name=body.name, sort_order=await pg.next_sort_order(session, project_id))
    session.add(g)
    await session.commit()
    return await _full(session, g)


@router.patch("/{project_id}/groups/{group_id}", summary="Configure a print group",
              description="Only the fields sent change. `targets` replaces the whole list; `envelope_x`/`envelope_y` null = auto "
                          "(the smallest bed among the printers the targets cover).",
              dependencies=[Depends(require_scope("projects:write"))])
async def patch_group(project_id: int, group_id: int, body: GroupPatch, session: AsyncSession = Depends(get_session)) -> dict:
    g = await _group(session, project_id, group_id)
    sent = body.model_fields_set
    if body.name is not None:
        g.name = body.name
    if "envelope_x" in sent or "envelope_y" in sent:
        x = body.envelope_x if "envelope_x" in sent else g.envelope_x
        y = body.envelope_y if "envelope_y" in sent else g.envelope_y
        if (x is None) != (y is None):
            raise HTTPException(422, "envelope_x and envelope_y are set together (or both null for auto)")
        g.envelope_x, g.envelope_y = x, y
    if body.mix_colours is not None:
        g.mix_colours = body.mix_colours
    if body.strategy is not None:
        g.strategy = body.strategy
    if "max_parts" in sent:
        g.max_parts = body.max_parts
    if "overrides" in sent:
        g.overrides = body.overrides
    if body.targets is not None:
        keys = [t.target_key for t in body.targets]
        if len(set(keys)) != len(keys):
            raise HTTPException(422, "A target is listed more than once")
        for key in keys:
            problem = await pg.check_target_key(session, key)
            if problem:
                raise HTTPException(422, problem)
        for old in await pg.targets_of(session, g.id):
            await session.delete(old)
        await session.flush()
        for i, t in enumerate(body.targets):
            session.add(ProjectGroupTarget(group_id=g.id, target_key=t.target_key, process_profile=t.process_profile, sort_order=i))
    await session.commit()
    return await _full(session, g)


@router.put("/{project_id}/groups/order", summary="Set the queue order of a project's print groups",
            dependencies=[Depends(require_scope("projects:write"))])
async def order_groups(project_id: int, body: GroupOrder, session: AsyncSession = Depends(get_session)) -> list[dict]:
    await _project(session, project_id)
    groups = {g.id: g for g in await pg.groups_of(session, project_id)}
    if sorted(body.group_ids) != sorted(groups):
        raise HTTPException(422, "group_ids must list every group of the project exactly once")
    for i, gid in enumerate(body.group_ids):
        groups[gid].sort_order = i
    await session.commit()
    return [await _full(session, groups[gid]) for gid in body.group_ids]


@router.delete("/{project_id}/groups/{group_id}", status_code=204, summary="Delete a print group (its items move to the Default Group)",
               responses={409: {"description": "The Default Group can't be deleted"}},
               dependencies=[Depends(require_scope("projects:write"))])
async def delete_group(project_id: int, group_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    g = await _group(session, project_id, group_id)
    if g.is_default:
        raise HTTPException(409, "The Default Group can't be deleted; rename it instead")
    default = await pg.ensure_default_group(session, project_id)
    for item in (await session.execute(select(ProjectItem).where(ProjectItem.group_id == g.id))).scalars():
        item.group_id = default.id
    await session.delete(g)
    await session.commit()
    return Response(status_code=204)
