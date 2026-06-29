from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_library_dir, get_orca_sidecar_url
from ...database import get_session
from ...models import Job, Project, ProjectItem, UploadedFile
from ...services.library_scanner import ACTIVE_JOB_STATUSES, LibraryScanner
from ...services.orca_sidecar_client import OrcaSidecarClient, SidecarError
from ...services.project_pack_builder import FilamentSlot, ProjectPackBuilder
from ...services.thumbnail_regen import regen_file_thumbnails

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

# ---------------------------------------------------------------------------
# Catalog cache (5-minute TTL, separate from SlicerService cache)
# ---------------------------------------------------------------------------
_catalog_cache: dict | None = None
_catalog_ts: float = 0.0
_CATALOG_TTL = 300.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    return re.sub(r"[\s_-]+", "-", slug).strip("-")[:80]


async def _get_catalog(sidecar_url: str) -> dict:
    global _catalog_cache, _catalog_ts
    if _catalog_cache is None or (time.monotonic() - _catalog_ts) > _CATALOG_TTL:
        client = OrcaSidecarClient(sidecar_url)
        try:
            _catalog_cache = await asyncio.to_thread(client.get_catalog)
            _catalog_ts = time.monotonic()
        except SidecarError as exc:
            raise HTTPException(502, f"Orca sidecar unreachable: {exc}") from exc
    return _catalog_cache


def _resolve_filament_name(catalog: dict, uuid: str) -> str | None:
    for entry in catalog.get("filament", []):
        if entry.get("uuid") == uuid:
            return entry.get("name")
    return None


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str
    machine_uuid: str
    process_uuid: str
    notes: Optional[str] = None


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    machine_uuid: Optional[str] = None
    process_uuid: Optional[str] = None
    notes: Optional[str] = None


class ProjectItemCreate(BaseModel):
    file_id: int
    quantity: int = 1
    filament_profile_uuid: str
    color_hex: str = "#FFFFFF"
    sort_order: int = 0

    @field_validator("quantity")
    @classmethod
    def qty_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("quantity must be at least 1")
        return v


class ProjectItemUpdate(BaseModel):
    quantity: Optional[int] = None
    filament_profile_uuid: Optional[str] = None
    color_hex: Optional[str] = None
    sort_order: Optional[int] = None

    @field_validator("quantity")
    @classmethod
    def qty_positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("quantity must be at least 1")
        return v


class ReorderEntry(BaseModel):
    id: int
    sort_order: int


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _item_dict(item: ProjectItem, file_name: str, filament_name: str | None) -> dict:
    return {
        "id": item.id,
        "project_id": item.project_id,
        "file_id": item.file_id,
        "file_name": file_name,
        "quantity": item.quantity,
        "filament_profile_uuid": item.filament_profile_uuid,
        "filament_display_name": filament_name,
        "color_hex": item.color_hex,
        "sort_order": item.sort_order,
    }


async def _load_items(
    session: AsyncSession,
    project_id: int,
    catalog: dict | None = None,
) -> list[dict]:
    rows = (
        await session.execute(
            select(ProjectItem)
            .where(ProjectItem.project_id == project_id)
            .order_by(ProjectItem.sort_order, ProjectItem.id)
        )
    ).scalars().all()
    result = []
    for item in rows:
        f = await session.get(UploadedFile, item.file_id)
        fname = f.original_filename if f else f"[file {item.file_id}]"
        fname_disp = catalog and _resolve_filament_name(catalog, item.filament_profile_uuid)
        result.append(_item_dict(item, fname, fname_disp))
    return result


async def _project_dict(
    project: Project,
    session: AsyncSession,
    catalog: dict | None = None,
) -> dict:
    items = await _load_items(session, project.id, catalog)
    return {
        "id": project.id,
        "name": project.name,
        "machine_uuid": project.machine_uuid,
        "process_uuid": project.process_uuid,
        "notes": project.notes,
        "result_file_id": project.result_file_id,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "items": items,
    }


async def _get_project_or_404(project_id: int, session: AsyncSession) -> Project:
    proj = await session.get(Project, project_id)
    if proj is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return proj


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------

@router.get("")
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (
        await session.execute(
            select(Project).order_by(Project.created_at.desc())
        )
    ).scalars().all()
    return [await _project_dict(p, session) for p in rows]


@router.post("", status_code=201)
async def create_project(
    body: ProjectCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    now = _now_iso()
    proj = Project(
        name=body.name,
        machine_uuid=body.machine_uuid,
        process_uuid=body.process_uuid,
        notes=body.notes,
        result_file_id=None,
        created_at=now,
        updated_at=now,
    )
    session.add(proj)
    await session.commit()
    await session.refresh(proj)
    return await _project_dict(proj, session)


@router.get("/{project_id}")
async def get_project(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    proj = await _get_project_or_404(project_id, session)
    sidecar_url = get_orca_sidecar_url()
    catalog = None
    if sidecar_url and _catalog_cache:
        catalog = _catalog_cache
    return await _project_dict(proj, session, catalog)


@router.patch("/{project_id}")
async def patch_project(
    project_id: int,
    body: ProjectPatch,
    session: AsyncSession = Depends(get_session),
) -> dict:
    proj = await _get_project_or_404(project_id, session)
    if body.name is not None:
        proj.name = body.name
    if body.machine_uuid is not None:
        proj.machine_uuid = body.machine_uuid
    if body.process_uuid is not None:
        proj.process_uuid = body.process_uuid
    if body.notes is not None:
        proj.notes = body.notes
    proj.updated_at = _now_iso()
    await session.commit()
    await session.refresh(proj)
    return await _project_dict(proj, session)


@router.delete("/{project_id}")
async def delete_project(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    proj = await _get_project_or_404(project_id, session)
    await session.delete(proj)
    await session.commit()
    return {"deleted": project_id}


# ---------------------------------------------------------------------------
# Project Item CRUD
# ---------------------------------------------------------------------------

@router.get("/{project_id}/items")
async def list_items(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    await _get_project_or_404(project_id, session)
    return await _load_items(session, project_id)


@router.post("/{project_id}/items", status_code=201)
async def add_item(
    project_id: int,
    body: ProjectItemCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    proj = await _get_project_or_404(project_id, session)
    f = await session.get(UploadedFile, body.file_id)
    if f is None:
        raise HTTPException(404, f"File {body.file_id} not found")
    item = ProjectItem(
        project_id=project_id,
        file_id=body.file_id,
        quantity=body.quantity,
        filament_profile_uuid=body.filament_profile_uuid,
        color_hex=body.color_hex,
        sort_order=body.sort_order,
    )
    session.add(item)
    proj.updated_at = _now_iso()
    await session.commit()
    await session.refresh(item)
    return _item_dict(item, f.original_filename, None)


@router.put("/{project_id}/items/{item_id}")
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
    if body.filament_profile_uuid is not None:
        item.filament_profile_uuid = body.filament_profile_uuid
    if body.color_hex is not None:
        item.color_hex = body.color_hex
    if body.sort_order is not None:
        item.sort_order = body.sort_order
    await session.commit()
    await session.refresh(item)
    f = await session.get(UploadedFile, item.file_id)
    fname = f.original_filename if f else f"[file {item.file_id}]"
    return _item_dict(item, fname, None)


@router.delete("/{project_id}/items/{item_id}")
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


@router.put("/{project_id}/items/reorder")
async def reorder_items(
    project_id: int,
    body: list[ReorderEntry],
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
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


# ---------------------------------------------------------------------------
# Assemble (arrange) — builds combined 3MF, sends to Orca, saves to library
# ---------------------------------------------------------------------------

@router.post("/{project_id}/assemble")
async def assemble_project(
    project_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> dict:
    proj = await _get_project_or_404(project_id, session)

    sidecar_url = get_orca_sidecar_url()
    if not sidecar_url:
        raise HTTPException(422, "ORCA_SIDECAR_URL is not configured — Orca sidecar required for assembly")

    # Load items ordered by sort_order, id
    item_rows = (
        await session.execute(
            select(ProjectItem)
            .where(ProjectItem.project_id == project_id)
            .order_by(ProjectItem.sort_order, ProjectItem.id)
        )
    ).scalars().all()

    if not item_rows:
        raise HTTPException(422, "Project has no items — add STL files before assembling")

    # Fetch catalog for bed dimensions + filament display names
    catalog = await _get_catalog(sidecar_url)

    # Resolve bed dimensions from machine profile
    machine_entry = next(
        (m for m in catalog.get("machine", []) if m.get("uuid") == proj.machine_uuid),
        None,
    )
    if machine_entry is None:
        raise HTTPException(
            422,
            f"Machine UUID {proj.machine_uuid!r} not found in Orca catalog — update project settings",
        )
    bed_x = float(machine_entry.get("bed_size_x") or 256)
    bed_y = float(machine_entry.get("bed_size_y") or 256)

    # Slot assignment: (filament_profile_uuid, color_hex) → slot_index (1-based)
    slot_map: dict[tuple[str, str], int] = {}
    filament_slots: list[FilamentSlot] = []
    for item in item_rows:
        key = (item.filament_profile_uuid, item.color_hex)
        if key not in slot_map:
            slot_index = len(slot_map) + 1
            slot_map[key] = slot_index
            display_name = _resolve_filament_name(catalog, item.filament_profile_uuid) or "Unknown"
            fil_entry = next(
                (f for f in catalog.get("filament", []) if f.get("uuid") == item.filament_profile_uuid),
                {},
            )
            filament_slots.append(FilamentSlot(
                uuid=item.filament_profile_uuid,
                display_name=display_name,
                filament_type=fil_entry.get("filament_type", "PLA"),
                color_hex=item.color_hex,
            ))

    # Resolve STL paths and validate
    builder_items = []
    for item in item_rows:
        f = await session.get(UploadedFile, item.file_id)
        if f is None:
            raise HTTPException(422, f"File {item.file_id} not found in library")
        if not f.original_filename.lower().endswith(".stl"):
            raise HTTPException(400, f"File {f.original_filename!r} is not an STL — only STL files are supported")
        stl_path = Path(f.stored_path)
        if not stl_path.exists():
            raise HTTPException(422, f"STL file {f.original_filename!r} is missing from disk")
        slot_index = slot_map[(item.filament_profile_uuid, item.color_hex)]
        builder_items.append({
            "file_path": stl_path,
            "quantity": item.quantity,
            "slot_index": slot_index,
        })

    # Build combined 3MF + call Orca arrange
    library_dir = get_library_dir()
    projects_dir = library_dir / "Projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        combined_path = Path(tmpdir) / "combined.3mf"
        ProjectPackBuilder().build(
            items=builder_items,
            bed_x=bed_x,
            bed_y=bed_y,
            filament_slots=filament_slots,
            out_path=combined_path,
        )
        client = OrcaSidecarClient(sidecar_url)
        try:
            arranged_bytes = await asyncio.to_thread(
                client.arrange, combined_path, True, True, 130.0
            )
        except SidecarError as exc:
            if "timed out" in str(exc).lower():
                raise HTTPException(504, "Assembly timed out — try fewer parts or reduce quantities")
            raise HTTPException(502, f"Orca sidecar error during assembly: {exc}") from exc

    # Determine output path; overwrite previous result unless an active job holds it
    out_filename = f"project-{_slugify(proj.name)}.3mf"
    if proj.result_file_id is not None:
        active = (
            await session.execute(
                select(Job.id)
                .where(
                    Job.uploaded_file_id == proj.result_file_id,
                    Job.status.in_(ACTIVE_JOB_STATUSES),
                )
                .limit(1)
            )
        ).first()
        if active is None:
            # Safe to replace: delete old record and its file
            old_file = await session.get(UploadedFile, proj.result_file_id)
            if old_file and Path(old_file.stored_path).exists():
                Path(old_file.stored_path).unlink(missing_ok=True)
            if old_file:
                await session.delete(old_file)
            proj.result_file_id = None
            await session.commit()

    out_path = LibraryScanner.unique_path(projects_dir, out_filename)
    out_path.write_bytes(arranged_bytes)

    # Parse plates from the arranged 3MF
    try:
        import zipfile
        with zipfile.ZipFile(out_path) as zf:
            names = zf.namelist()
        plate_nums = sorted(set(
            int(n.split("plate_")[1].split(".")[0])
            for n in names
            if "Metadata/plate_" in n and ".png" in n
        ))
    except Exception:
        plate_nums = []

    now = _now_iso()
    rel = out_path.relative_to(library_dir).as_posix()
    new_file = UploadedFile(
        original_filename=out_filename,
        stored_path=str(out_path),
        plates=[{"plate_number": p, "thumbnail_path": None} for p in plate_nums],
        uploaded_at=now,
        relative_path=rel,
        folder="/Projects",
        size_bytes=out_path.stat().st_size,
        content_hash="",
        mtime=out_path.stat().st_mtime,
        missing=False,
    )
    session.add(new_file)
    await session.commit()
    await session.refresh(new_file)

    proj.result_file_id = new_file.id
    proj.updated_at = now
    await session.commit()

    background_tasks.add_task(regen_file_thumbnails, new_file.id)

    return {
        "project_id": proj.id,
        "result_file_id": new_file.id,
        "plate_count": len(plate_nums),
        "file": {
            "id": new_file.id,
            "original_filename": new_file.original_filename,
            "folder": new_file.folder,
            "plate_count": len(plate_nums),
        },
    }
