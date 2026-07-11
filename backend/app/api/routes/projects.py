from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_library_dir, get_laminus_sidecar_url
from ...database import get_session
from ...models import GcodeFile, Job, Order, Project, ProjectItem, UploadedFile
from ...services.library_scanner import ACTIVE_JOB_STATUSES, LibraryScanner
from ...services.laminus_sidecar_client import LaminusSidecarClient, SidecarError
from ...services.thumbnail_regen import regen_file_thumbnails

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/projects", tags=["projects"])



def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    return re.sub(r"[\s_-]+", "-", slug).strip("-")[:80]


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
    machine_uuid: Optional[str] = None
    process_uuid: Optional[str] = None
    notes: Optional[str] = None
    source_app: Optional[str] = None
    source_user: Optional[str] = None
    source_layout_id: Optional[int] = None


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
        "quantity_completed": item.quantity_completed,
        "quantity_failed": item.quantity_failed,
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
    jobs_total = (await session.execute(
        select(func.count()).where(Job.project_id == project.id)
    )).scalar() or 0
    jobs_complete = (await session.execute(
        select(func.count()).where(Job.project_id == project.id, Job.status == "complete")
    )).scalar() or 0
    gcode_rows = (await session.execute(
        select(GcodeFile).join(Job, Job.id == GcodeFile.job_id).where(Job.project_id == project.id)
    )).scalars().all()
    total_grams = sum(g.filament_grams for g in gcode_rows if g.filament_grams is not None) or None
    total_seconds = sum(g.estimated_seconds for g in gcode_rows if g.estimated_seconds is not None) or None
    return {
        "id": project.id,
        "name": project.name,
        "machine_uuid": project.machine_uuid,
        "process_uuid": project.process_uuid,
        "notes": project.notes,
        "result_file_id": project.result_file_id,
        "order_id": project.order_id,
        "source_app": project.source_app,
        "source_user": project.source_user,
        "source_layout_id": project.source_layout_id,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "items": items,
        "jobs_total": jobs_total,
        "jobs_complete": jobs_complete,
        "filament_grams": round(total_grams, 2) if total_grams is not None else None,
        "estimated_seconds": total_seconds,
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
        source_app=body.source_app,
        source_user=body.source_user,
        source_layout_id=body.source_layout_id,
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
    catalog = None
    if get_laminus_sidecar_url():
        from .laminus import _catalog_dict
        catalog = _catalog_dict
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
# Generate — packs STLs one 3MF per filament group via Orca, saves to library,
# and queues one job per plate. The only entry point for pack/assemble.
# ---------------------------------------------------------------------------

async def _max_queue_position(session: AsyncSession) -> float:
    result = await session.execute(select(func.max(Job.queue_position)))
    return result.scalar_one_or_none() or 0.0


def _parse_plate_nums(path: Path) -> list[int]:
    try:
        import zipfile
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        return sorted(set(
            int(n.split("plate_")[1].split(".")[0])
            for n in names
            if "Metadata/plate_" in n and ".png" in n
        ))
    except Exception:
        return []


@router.post("/{project_id}/generate")
async def generate_project(
    project_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> dict:
    proj = await _get_project_or_404(project_id, session)

    # Create a linked internal Order on first generate so jobs appear in the Orders view
    if proj.order_id is None:
        now_ts = _now_iso()
        order = Order(
            order_type="internal",
            customer="",
            title=proj.name,
            on_hold=False,
            parts=[],
            created_at=now_ts,
            updated_at=now_ts,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        proj.order_id = order.id
        await session.commit()

    sidecar_url = get_laminus_sidecar_url()
    if not sidecar_url:
        raise HTTPException(422, "LAMINUS_SIDECAR_URL is not configured — Laminus sidecar required for generation")

    # Load items ordered by sort_order, id
    item_rows = (
        await session.execute(
            select(ProjectItem)
            .where(ProjectItem.project_id == project_id)
            .order_by(ProjectItem.sort_order, ProjectItem.id)
        )
    ).scalars().all()

    if not item_rows:
        raise HTTPException(422, "Project has no items — add STL files before generating")

    # Group items by (filament_profile_uuid, color_hex), preserving item order.
    # Items with no profile are skipped — the caller is expected to show a
    # "assign filament profiles" banner; those items simply produce no job.
    groups: dict[tuple[str, str], list[ProjectItem]] = {}
    for item in item_rows:
        if not item.filament_profile_uuid:
            continue
        groups.setdefault((item.filament_profile_uuid, item.color_hex), []).append(item)

    if not groups:
        raise HTTPException(
            status_code=422,
            detail="No items have filament profiles assigned — assign profiles in Themis before generating",
        )

    # Resolve STL paths per group (repeated per quantity) and validate
    group_paths: dict[tuple[str, str], list[Path]] = {}
    for key, group_items in groups.items():
        paths: list[Path] = []
        for item in group_items:
            f = await session.get(UploadedFile, item.file_id)
            if f is None:
                raise HTTPException(422, f"File {item.file_id} not found in library")
            if not f.original_filename.lower().endswith(".stl"):
                raise HTTPException(400, f"File {f.original_filename!r} is not an STL — only STL files are supported")
            stl_path = Path(f.stored_path)
            if not stl_path.exists():
                raise HTTPException(422, f"STL file {f.original_filename!r} is missing from disk")
            paths.extend([stl_path] * item.quantity)
        group_paths[key] = paths

    # Clean up the legacy single-result file unless an active job holds it
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
            old_file = await session.get(UploadedFile, proj.result_file_id)
            if old_file and Path(old_file.stored_path).exists():
                Path(old_file.stored_path).unlink(missing_ok=True)
            if old_file:
                await session.delete(old_file)
        # No single result file in the new flow
        proj.result_file_id = None
        await session.commit()

    library_dir = get_library_dir()
    projects_dir = library_dir / "Projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    client = LaminusSidecarClient(sidecar_url)
    jobs_out: list[dict] = []
    files_out: list[dict] = []

    for (fil_uuid, color_hex), stl_paths in group_paths.items():
        group_items = groups[(fil_uuid, color_hex)]

        try:
            packed_bytes = await asyncio.to_thread(
                client.pack_stls_by_uuid,
                stl_paths,
                proj.machine_uuid,
                proj.process_uuid,
                [fil_uuid],
            )
        except SidecarError as exc:
            if "timed out" in str(exc).lower():
                raise HTTPException(504, "Generation timed out — try fewer parts or reduce quantities")
            raise HTTPException(502, f"Orca sidecar error during generation: {exc}") from exc

        label = color_hex.lstrip("#") if color_hex else fil_uuid[:8]
        out_filename = f"project-{_slugify(proj.name)}-{label}.3mf"
        out_path = LibraryScanner.unique_path(projects_dir, out_filename)
        out_path.write_bytes(packed_bytes)

        plate_nums = _parse_plate_nums(out_path)

        now = _now_iso()
        rel = out_path.relative_to(library_dir).as_posix()
        new_file = UploadedFile(
            original_filename=out_path.name,
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

        background_tasks.add_task(regen_file_thumbnails, new_file.id)

        files_out.append({
            "id": new_file.id,
            "original_filename": new_file.original_filename,
            "folder": new_file.folder,
            "plate_count": len(plate_nums),
        })

        # Compute per-plate quantity distribution for each item
        effective_plates = plate_nums or [1]
        num_plates = len(effective_plates)
        plate_item_qtys: list[dict[str, int]] = []
        for i in range(num_plates):
            plate_q: dict[str, int] = {}
            for item in group_items:
                base = item.quantity // num_plates
                extra = 1 if i < (item.quantity % num_plates) else 0
                plate_q[str(item.id)] = base + extra
            plate_item_qtys.append(plate_q)

        # One queued job per plate
        next_pos = await _max_queue_position(session) + 1.0
        new_jobs: list[Job] = []
        for plate_idx, plate_num in enumerate(effective_plates):
            job = Job(
                uploaded_file_id=new_file.id,
                plate_number=plate_num,
                project_id=proj.id,
                order_id=proj.order_id,
                queue_position=next_pos,
                status="queued",
                created_at=now,
                updated_at=now,
                project_item_quantities=json.dumps(plate_item_qtys[plate_idx]),
            )
            session.add(job)
            new_jobs.append(job)
            next_pos += 1.0
        await session.commit()
        jobs_out.extend({
            "id": j.id,
            "uploaded_file_id": j.uploaded_file_id,
            "plate_number": j.plate_number,
            "queue_position": j.queue_position,
            "status": j.status,
        } for j in new_jobs)

    proj.updated_at = _now_iso()
    await session.commit()

    return {"project_id": proj.id, "order_id": proj.order_id, "jobs": jobs_out, "files": files_out}
