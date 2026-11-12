"""A project's children: items (the models to print), links and parts (bought-in components), with their CRUD and reorder routes.

Part of the `projects` routes: registered on the same `router` and imported at the bottom of projects.py.
What tests replace on that module (get_library_store, get_project_constructor, queue_engine, refresh_content_hash, secrets) is read as `_projects.<name>` at call time, so one patch
point covers every projects route.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ._filament_ask import ask_columns, normalize as _normalize_ask
from ...auth import require_scope
from ...database import get_session
from ...models import ProjectGroup, ProjectItem, ProjectLink, ProjectPart, UploadedFile

from . import projects as _projects
from .projects import (  # noqa: I001
    _item_group,
    _item_kind,
    ProjectItemCreate,
    ProjectItemUpdate,
    ProjectLinkCreate,
    ProjectLinkUpdate,
    ProjectPartCreate,
    ProjectPartUpdate,
    ReorderEntry,
    _RULES,
    _get_project_or_404,
    _item_dict,
    _link_dict,
    _load_items,
    _load_links,
    _load_parts,
    _now_iso,
    _part_dict,
    router,
)


# ---------------------------------------------------------------------------
# Project Item CRUD
# ---------------------------------------------------------------------------

@router.get(
    "/{project_id}/items",
    summary="List project items",
    responses={
        404: {"description": "Project not found"},
    },
    dependencies=[Depends(require_scope("projects:read"))],
)
async def list_items(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    await _get_project_or_404(project_id, session)
    return await _load_items(session, project_id)


@router.post(
    "/{project_id}/items",
    status_code=201,
    summary="Add item to project",
    responses={
        404: {"description": "Project or file not found"},
        422: {"description": "The file is pre-sliced (.gcode / .gcode.3mf) — only models can be packed"},
    },
    dependencies=[Depends(require_scope("projects:write"))],
)
async def add_item(
    project_id: int,
    body: ProjectItemCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    proj = await _get_project_or_404(project_id, session)
    f = await session.get(UploadedFile, body.file_id)
    if f is None:
        raise HTTPException(404, f"File {body.file_id} not found")
    kind, ref, rule = _item_kind(f, body.kind, body.source_ref, body.pack_rule)
    if kind in ("3mf_object", "3mf_plate"):
        await _projects.refresh_content_hash(f, _projects.get_library_store())       # what "the file changed since it was picked" compares to
    item = ProjectItem(
        project_id=project_id,
        file_id=body.file_id,
        quantity=body.quantity,
        **ask_columns(body),
        sort_order=body.sort_order,
        kind=kind, source_ref=ref, pack_rule=rule,
        source_hash=f.content_hash if kind in ("3mf_object", "3mf_plate") else None,
        group_id=await _item_group(session, project_id, body.group_id),   # new items land in the Default Group
    )
    session.add(item)
    proj.updated_at = _now_iso()
    await session.commit()
    await session.refresh(item)
    return _item_dict(item, f.original_filename)


# NOTE: declared before PUT /{project_id}/items/{item_id} on purpose — Starlette matches routes in
# declaration order, so "/items/reorder" would otherwise be captured by {item_id} and always 422.
@router.put(
    "/{project_id}/items/reorder",
    summary="Reorder project items",
    responses={
        404: {"description": "Project not found"},
        422: {"description": "One or more item IDs do not belong to this project"},
    },
    dependencies=[Depends(require_scope("projects:write"))],
)
async def reorder_items(
    project_id: int,
    body: list[ReorderEntry],
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Set explicit sort_order values for project items. Returns the full updated item list."""
    await _get_project_or_404(project_id, session)
    item_ids = [e.id for e in body]
    rows = (
        await session.execute(
            select(ProjectItem).where(
                ProjectItem.id.in_(item_ids),
                ProjectItem.project_id == project_id,
            )
        )
    ).scalars().all()
    if len(rows) != len(body):
        raise HTTPException(422, "One or more item IDs do not belong to this project")
    order_map = {e.id: e.sort_order for e in body}
    for item in rows:
        item.sort_order = order_map[item.id]
    await session.commit()
    return await _load_items(session, project_id)


@router.put(
    "/{project_id}/items/{item_id}",
    summary="Update project item",
    responses={
        404: {"description": "Project or item not found"},
    },
    dependencies=[Depends(require_scope("projects:write"))],
)
async def update_item(
    project_id: int,
    item_id: int,
    body: ProjectItemUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_project_or_404(project_id, session)
    item = await session.get(ProjectItem, item_id)
    if item is None or item.project_id != project_id:
        raise HTTPException(404, f"Item {item_id} not found in project {project_id}")
    if body.quantity is not None:
        item.quantity = body.quantity
    sent = body.model_fields_set & {"filament_manufacturer", "filament_type", "filament_subtype", "filament_color"}
    if sent:
        merged = {f: (getattr(body, f) if f in sent else getattr(item, c)) for f, c in zip(
            ("filament_manufacturer", "filament_type", "filament_subtype", "filament_color"),
            ("ask_manufacturer", "ask_type", "ask_subtype", "ask_color"))}
        if "filament_subtype" not in sent and not _normalize_ask(None, merged["filament_type"], "any", None).type:
            merged["filament_subtype"] = None                      # no type left (cleared, or the legacy "any"): its subtype goes too
        try:
            ask = _normalize_ask(merged["filament_manufacturer"], merged["filament_type"], merged["filament_subtype"],
                                 merged["filament_color"])
        except ValueError as e:
            raise HTTPException(422, str(e))
        item.ask_manufacturer, item.ask_type, item.ask_subtype, item.ask_color = ask.manufacturer, ask.type, ask.subtype, ask.color
    if body.sort_order is not None:
        item.sort_order = body.sort_order
    if body.pack_rule is not None:
        if body.pack_rule not in _RULES.get(item.kind or "stl", ()):
            raise HTTPException(422, f"A {item.kind or 'stl'} item can't use the {body.pack_rule!r} rule")
        item.pack_rule = body.pack_rule
    if body.group_id is not None:
        group = await session.get(ProjectGroup, body.group_id)
        if group is None or group.project_id != project_id:
            raise HTTPException(422, f"Group {body.group_id} is not a print group of project {project_id}")
        item.group_id = group.id
    await session.commit()
    await session.refresh(item)
    f = await session.get(UploadedFile, item.file_id)
    fname = f.original_filename if f else f"[file {item.file_id}]"
    return _item_dict(item, fname)


@router.delete(
    "/{project_id}/items/{item_id}",
    summary="Remove item from project",
    responses={
        404: {"description": "Project or item not found"},
    },
    dependencies=[Depends(require_scope("projects:write"))],
)
async def delete_item(
    project_id: int,
    item_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_project_or_404(project_id, session)
    item = await session.get(ProjectItem, item_id)
    if item is None or item.project_id != project_id:
        raise HTTPException(404, f"Item {item_id} not found in project {project_id}")
    await session.delete(item)
    await session.commit()
    return {"deleted": item_id}


# ---------------------------------------------------------------------------
# Project Link CRUD
# ---------------------------------------------------------------------------

@router.get(
    "/{project_id}/links",
    summary="List project links",
    responses={
        404: {"description": "Project not found"},
    },
    dependencies=[Depends(require_scope("projects:read"))],
)
async def list_links(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    await _get_project_or_404(project_id, session)
    return await _load_links(session, project_id)


@router.post(
    "/{project_id}/links",
    status_code=201,
    summary="Add project link",
    responses={
        404: {"description": "Project not found"},
    },
    dependencies=[Depends(require_scope("projects:write"))],
)
async def add_link(
    project_id: int,
    body: ProjectLinkCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_project_or_404(project_id, session)
    link = ProjectLink(
        project_id=project_id,
        url=body.url,
        label=body.label,
        sort_order=body.sort_order,
        created_at=_now_iso(),
    )
    session.add(link)
    await session.commit()
    await session.refresh(link)
    return _link_dict(link)


@router.put(
    "/{project_id}/links/{link_id}",
    summary="Update project link",
    responses={
        404: {"description": "Project or link not found"},
    },
    dependencies=[Depends(require_scope("projects:write"))],
)
async def update_link(
    project_id: int,
    link_id: int,
    body: ProjectLinkUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_project_or_404(project_id, session)
    link = await session.get(ProjectLink, link_id)
    if link is None or link.project_id != project_id:
        raise HTTPException(404, f"Link {link_id} not found in project {project_id}")
    if body.url is not None:
        link.url = body.url
    if body.label is not None:
        link.label = body.label
    if body.sort_order is not None:
        link.sort_order = body.sort_order
    await session.commit()
    await session.refresh(link)
    return _link_dict(link)


@router.delete(
    "/{project_id}/links/{link_id}",
    summary="Delete project link",
    responses={
        404: {"description": "Project or link not found"},
    },
    dependencies=[Depends(require_scope("projects:write"))],
)
async def delete_link(
    project_id: int,
    link_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_project_or_404(project_id, session)
    link = await session.get(ProjectLink, link_id)
    if link is None or link.project_id != project_id:
        raise HTTPException(404, f"Link {link_id} not found in project {project_id}")
    await session.delete(link)
    await session.commit()
    return {"deleted": link_id}


# ---------------------------------------------------------------------------
# Project Part CRUD (non-3D-printed parts — hardware needed for the assembly)
# ---------------------------------------------------------------------------

@router.get(
    "/{project_id}/parts",
    summary="List project parts",
    responses={
        404: {"description": "Project not found"},
    },
    dependencies=[Depends(require_scope("projects:read"))],
)
async def list_parts(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    await _get_project_or_404(project_id, session)
    return await _load_parts(session, project_id)


@router.post(
    "/{project_id}/parts",
    status_code=201,
    summary="Add non-printed part to project",
    responses={
        404: {"description": "Project not found"},
    },
    dependencies=[Depends(require_scope("projects:write"))],
)
async def add_part(
    project_id: int,
    body: ProjectPartCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_project_or_404(project_id, session)
    part = ProjectPart(
        project_id=project_id,
        name=body.name,
        quantity=body.quantity,
        allocated=body.allocated,
        sort_order=body.sort_order,
        unit_cost=body.unit_cost,
        created_at=_now_iso(),
    )
    session.add(part)
    await session.commit()
    await session.refresh(part)
    return _part_dict(part)


@router.put(
    "/{project_id}/parts/{part_id}",
    summary="Update project part",
    responses={
        404: {"description": "Project or part not found"},
    },
    dependencies=[Depends(require_scope("projects:write"))],
)
async def update_part(
    project_id: int,
    part_id: int,
    body: ProjectPartUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_project_or_404(project_id, session)
    part = await session.get(ProjectPart, part_id)
    if part is None or part.project_id != project_id:
        raise HTTPException(404, f"Part {part_id} not found in project {project_id}")
    if body.name is not None:
        part.name = body.name
    if body.quantity is not None:
        part.quantity = body.quantity
    if body.allocated is not None:
        part.allocated = body.allocated
    if body.sort_order is not None:
        part.sort_order = body.sort_order
    if "unit_cost" in body.model_fields_set:
        part.unit_cost = body.unit_cost
    await session.commit()
    await session.refresh(part)
    return _part_dict(part)


@router.delete(
    "/{project_id}/parts/{part_id}",
    summary="Remove part from project",
    responses={
        404: {"description": "Project or part not found"},
    },
    dependencies=[Depends(require_scope("projects:write"))],
)
async def delete_part(
    project_id: int,
    part_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_project_or_404(project_id, session)
    part = await session.get(ProjectPart, part_id)
    if part is None or part.project_id != project_id:
        raise HTTPException(404, f"Part {part_id} not found in project {project_id}")
    await session.delete(part)
    await session.commit()
    return {"deleted": part_id}
