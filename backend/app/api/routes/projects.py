from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_library_dir, get_laminus_sidecar_url
from ...database import get_session
from ...models import GcodeFile, Job, JobPrinterConfig, Printer, Project, ProjectItem, ProjectLink, UploadedFile
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


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str
    customer: str = ""
    order_type: str = "internal"   # "customer" | "internal"
    on_hold: bool = False
    due_date: Optional[str] = None
    notes: Optional[str] = None
    source_app: Optional[str] = None
    source_user: Optional[str] = None
    source_layout_id: Optional[int] = None


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    customer: Optional[str] = None
    order_type: Optional[str] = None
    on_hold: Optional[bool] = None
    due_date: Optional[str] = None
    notes: Optional[str] = None


class ProjectItemCreate(BaseModel):
    file_id: int
    quantity: int = 1
    filament_type: str = "any"
    filament_color: str = "any"
    filament_id: Optional[int] = None
    sort_order: int = 0

    @field_validator("quantity")
    @classmethod
    def qty_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("quantity must be at least 1")
        return v


class ProjectItemUpdate(BaseModel):
    quantity: Optional[int] = None
    filament_type: Optional[str] = None
    filament_color: Optional[str] = None
    filament_id: Optional[int] = None
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


class ProjectLinkCreate(BaseModel):
    url: str
    label: Optional[str] = None
    sort_order: int = 0


class ProjectLinkUpdate(BaseModel):
    url: Optional[str] = None
    label: Optional[str] = None
    sort_order: Optional[int] = None


class GenerateRequest(BaseModel):
    eligible_printer_ids: list[int] = []


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _item_dict(item: ProjectItem, file_name: str) -> dict:
    return {
        "id": item.id,
        "project_id": item.project_id,
        "file_id": item.file_id,
        "file_name": file_name,
        "quantity": item.quantity,
        "quantity_completed": item.quantity_completed,
        "quantity_failed": item.quantity_failed,
        "filament_type": item.filament_type,
        "filament_color": item.filament_color,
        "filament_id": item.filament_id,
        "sort_order": item.sort_order,
    }


def _link_dict(link: ProjectLink) -> dict:
    return {
        "id": link.id,
        "project_id": link.project_id,
        "url": link.url,
        "label": link.label,
        "sort_order": link.sort_order,
        "created_at": link.created_at,
    }


async def _load_links(session: AsyncSession, project_id: int) -> list[dict]:
    rows = (
        await session.execute(
            select(ProjectLink)
            .where(ProjectLink.project_id == project_id)
            .order_by(ProjectLink.sort_order, ProjectLink.id)
        )
    ).scalars().all()
    return [_link_dict(lnk) for lnk in rows]


async def _load_items(session: AsyncSession, project_id: int) -> list[dict]:
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
        result.append(_item_dict(item, fname))
    return result


async def _project_dict(project: Project, session: AsyncSession) -> dict:
    items = await _load_items(session, project.id)
    links = await _load_links(session, project.id)
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
        "customer": project.customer,
        "order_type": project.order_type,
        "on_hold": project.on_hold,
        "due_date": project.due_date,
        "notes": project.notes,
        "result_file_id": project.result_file_id,
        "source_app": project.source_app,
        "source_user": project.source_user,
        "source_layout_id": project.source_layout_id,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "items": items,
        "links": links,
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

@router.get("", summary="List projects")
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """All projects ordered by creation date descending, each with items, links,
    job counts, and aggregated filament/time estimates."""
    rows = (
        await session.execute(
            select(Project).order_by(Project.created_at.desc())
        )
    ).scalars().all()
    return [await _project_dict(p, session) for p in rows]


@router.post("", status_code=201, summary="Create project")
async def create_project(
    body: ProjectCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    now = _now_iso()
    proj = Project(
        name=body.name,
        customer=body.customer,
        order_type=body.order_type,
        on_hold=body.on_hold,
        due_date=body.due_date,
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


@router.get(
    "/{project_id}",
    summary="Get project",
    responses={
        404: {"description": "Project not found"},
    },
)
async def get_project(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    proj = await _get_project_or_404(project_id, session)
    return await _project_dict(proj, session)


@router.patch(
    "/{project_id}",
    summary="Update project",
    responses={
        404: {"description": "Project not found"},
    },
)
async def patch_project(
    project_id: int,
    body: ProjectPatch,
    session: AsyncSession = Depends(get_session),
) -> dict:
    proj = await _get_project_or_404(project_id, session)
    if body.name is not None:
        proj.name = body.name
    if body.customer is not None:
        proj.customer = body.customer
    if body.order_type is not None:
        proj.order_type = body.order_type
    if body.on_hold is not None:
        proj.on_hold = body.on_hold
    if body.due_date is not None:
        proj.due_date = body.due_date
    if body.notes is not None:
        proj.notes = body.notes
    proj.updated_at = _now_iso()
    await session.commit()
    await session.refresh(proj)
    return await _project_dict(proj, session)


@router.delete(
    "/{project_id}",
    summary="Delete project",
    responses={
        404: {"description": "Project not found"},
    },
)
async def delete_project(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    proj = await _get_project_or_404(project_id, session)
    await session.delete(proj)
    await session.commit()
    return {"deleted": project_id}


@router.get(
    "/{project_id}/jobs",
    summary="List project jobs",
    responses={
        404: {"description": "Project not found"},
    },
)
async def list_project_jobs(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """All jobs belonging to this project, ordered by job ID."""
    await _get_project_or_404(project_id, session)
    rows = (
        await session.execute(
            select(Job)
            .where(Job.project_id == project_id)
            .order_by(Job.id)
        )
    ).scalars().all()
    result = []
    for job in rows:
        f = await session.get(UploadedFile, job.uploaded_file_id)
        fname = f.original_filename if f else None
        item_quantities: dict[str, int] = {}
        if job.project_item_quantities:
            try:
                item_quantities = json.loads(job.project_item_quantities)
            except Exception:
                pass
        result.append({
            "id": job.id,
            "plate_number": job.plate_number,
            "status": job.status,
            "queue_position": job.queue_position,
            "assigned_printer_id": job.assigned_printer_id,
            "block_reason": job.block_reason,
            "outcome": job.outcome,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "completed_at": job.completed_at,
            "file_name": fname,
            "total_parts": sum(item_quantities.values()),
        })
    return result


# ---------------------------------------------------------------------------
# Project Item CRUD
# ---------------------------------------------------------------------------

@router.get(
    "/{project_id}/items",
    summary="List project items",
    responses={
        404: {"description": "Project not found"},
    },
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
    },
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
    item = ProjectItem(
        project_id=project_id,
        file_id=body.file_id,
        quantity=body.quantity,
        filament_type=body.filament_type,
        filament_color=body.filament_color,
        filament_id=body.filament_id,
        sort_order=body.sort_order,
    )
    session.add(item)
    proj.updated_at = _now_iso()
    await session.commit()
    await session.refresh(item)
    return _item_dict(item, f.original_filename)


@router.put(
    "/{project_id}/items/{item_id}",
    summary="Update project item",
    responses={
        404: {"description": "Project or item not found"},
    },
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
    if body.filament_type is not None:
        item.filament_type = body.filament_type
    if body.filament_color is not None:
        item.filament_color = body.filament_color
    if body.filament_id is not None:
        item.filament_id = body.filament_id
    if body.sort_order is not None:
        item.sort_order = body.sort_order
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


@router.put(
    "/{project_id}/items/reorder",
    summary="Reorder project items",
    responses={
        404: {"description": "Project not found"},
        422: {"description": "One or more item IDs do not belong to this project"},
    },
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


# ---------------------------------------------------------------------------
# Project Link CRUD
# ---------------------------------------------------------------------------

@router.get(
    "/{project_id}/links",
    summary="List project links",
    responses={
        404: {"description": "Project not found"},
    },
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
# Generate — packs STLs one 3MF per filament group via Orca, saves to library,
# and queues one job per plate.
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


def _filament_label(fil_type: str, fil_color: str, fil_id: int | None) -> str:
    """Short string for use in generated 3MF filenames."""
    if fil_id is not None:
        return f"f{fil_id}"
    parts = []
    if fil_type != "any":
        parts.append(fil_type.lower())
    if fil_color != "any":
        parts.append(fil_color.lstrip("#"))
    return "-".join(parts) if parts else "any"


@router.post(
    "/{project_id}/generate",
    summary="Pack STLs and queue jobs",
    responses={
        404: {"description": "Project not found or a referenced file is missing"},
        422: {"description": "Project has no items or contains non-STL files"},
        502: {"description": "Orca sidecar error during generation"},
        504: {"description": "Generation timed out"},
    },
)
async def generate_project(
    project_id: int,
    body: GenerateRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Pack project STL items into 3MF files (one per filament group), save them to the library,
    and queue one job per plate. Returns created job and file IDs plus the bed dimensions used
    for packing."""
    proj = await _get_project_or_404(project_id, session)

    sidecar_url = get_laminus_sidecar_url()
    if not sidecar_url:
        raise HTTPException(422, "LAMINUS_SIDECAR_URL is not configured — Laminus sidecar required for generation")

    # Resolve eligible printers and compute the smallest bed dimensions.
    eligible_printers: list[Printer] = []
    if body.eligible_printer_ids:
        rows = (await session.execute(
            select(Printer).where(Printer.id.in_(body.eligible_printer_ids))
        )).scalars().all()
        eligible_printers = list(rows)

    if eligible_printers:
        pack_bed_x = min(p.bed_x_mm for p in eligible_printers)
        pack_bed_y = min(p.bed_y_mm for p in eligible_printers)
    else:
        pack_bed_x, pack_bed_y = 256.0, 256.0

    item_rows = (
        await session.execute(
            select(ProjectItem)
            .where(ProjectItem.project_id == project_id)
            .order_by(ProjectItem.sort_order, ProjectItem.id)
        )
    ).scalars().all()

    if not item_rows:
        raise HTTPException(422, "Project has no items — add STL files before generating")

    # Group items by filament requirement (type, color, specific spoolman id)
    groups: dict[tuple[str, str, int | None], list[ProjectItem]] = {}
    for item in item_rows:
        key = (item.filament_type, item.filament_color, item.filament_id)
        groups.setdefault(key, []).append(item)

    # Resolve STL paths per group
    group_paths: dict[tuple[str, str, int | None], list[Path]] = {}
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

    # Clean up legacy single-result file unless an active job holds it
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
        proj.result_file_id = None
        await session.commit()

    library_dir = get_library_dir()
    job_pack_dir = library_dir / "Job Pack 3MFs"
    job_pack_dir.mkdir(parents=True, exist_ok=True)

    client = LaminusSidecarClient(sidecar_url)
    jobs_out: list[dict] = []
    files_out: list[dict] = []

    for (fil_type, fil_color, fil_id), stl_paths in group_paths.items():
        group_items = groups[(fil_type, fil_color, fil_id)]

        try:
            if proj.machine_uuid and proj.process_uuid:
                # Legacy path: project has OrcaSlicer profiles embedded
                packed_bytes = await asyncio.to_thread(
                    client.pack_stls_by_uuid,
                    stl_paths,
                    proj.machine_uuid,
                    proj.process_uuid,
                    [],
                )
            else:
                # Geometry-only pack; slicing profiles applied at dispatch time
                packed_bytes = await asyncio.to_thread(
                    client.pack_stls,
                    stl_paths,
                    pack_bed_x,
                    pack_bed_y,
                )
        except SidecarError as exc:
            if "timed out" in str(exc).lower():
                raise HTTPException(504, "Generation timed out — try fewer parts or reduce quantities")
            raise HTTPException(502, f"Orca sidecar error during generation: {exc}") from exc

        label = _filament_label(fil_type, fil_color, fil_id)
        out_filename = f"project-{_slugify(proj.name)}-{label}.3mf"
        # Write to a temp subdirectory; renamed to the job-ID subfolder once IDs are known.
        tmp_subdir = job_pack_dir / f"_tmp_{_uuid.uuid4().hex[:10]}"
        tmp_subdir.mkdir(parents=True, exist_ok=True)
        out_path = tmp_subdir / out_filename
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
            folder="/Job Pack 3MFs",
            size_bytes=out_path.stat().st_size,
            content_hash="",
            mtime=out_path.stat().st_mtime,
            missing=False,
        )
        session.add(new_file)
        await session.commit()
        await session.refresh(new_file)

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
        # Commit jobs so the DB assigns their autoincrement IDs.
        await session.commit()
        # Refresh each job so j.id is populated.
        for j in new_jobs:
            await session.refresh(j)

        # Rename temp dir to the job-ID subfolder now that IDs are known.
        job_id_label = str(new_jobs[0].id) if len(new_jobs) == 1 else f"{new_jobs[0].id}-{new_jobs[-1].id}"
        final_subdir = job_pack_dir / job_id_label
        tmp_subdir.rename(final_subdir)
        final_path = final_subdir / out_filename
        new_file.stored_path = str(final_path)
        new_file.relative_path = final_path.relative_to(library_dir).as_posix()
        new_file.folder = f"/Job Pack 3MFs/{job_id_label}"
        await session.commit()

        background_tasks.add_task(regen_file_thumbnails, new_file.id)

        files_out.append({
            "id": new_file.id,
            "original_filename": new_file.original_filename,
            "folder": new_file.folder,
            "plate_count": len(plate_nums),
        })

        # Create printer configs for each eligible printer × job.
        for j in new_jobs:
            for p in eligible_printers:
                session.add(JobPrinterConfig(
                    job_id=j.id,
                    printer_id=p.id,
                    print_profile=p.current_orca_printer_profile or "",
                ))
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

    return {
        "project_id": proj.id,
        "jobs": jobs_out,
        "files": files_out,
        "eligible_printer_ids": [p.id for p in eligible_printers],
        "pack_bed_x": pack_bed_x,
        "pack_bed_y": pack_bed_y,
    }
