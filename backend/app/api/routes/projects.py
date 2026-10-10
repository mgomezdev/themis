from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ._materials import material_columns, stored
from ...services.inventory import refs as inventory_refs
from ...auth import require_scope
from ...config import get_library_dir
from ...database import get_session
from ...models import PROJECT_STAGES, Customer, Job, JobModelTarget, JobPrinterConfig, Printer, Project, ProjectItem, ProjectLink, ProjectPart, QueueConfig, SlicedVersion, UploadedFile
from ...services import idempotency, job_costs, model_targets, project_events, slice_cache
from ...services.payments import adopt_manual_amount, has_payments, sync_project_totals
from ...services.library_scanner import (
    ACTIVE_JOB_STATUSES, LibraryScanner, fresh_content_hash, is_presliced_name, library_abs_path, refresh_content_hash,
)
from ...services.providers.slicing import SlicingProviderError, get_slicing_provider
from ...services.queue_engine import queue_engine
from ...services.thumbnail_regen import regen_file_thumbnails

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

PAYMENT_STATUSES = {"unpaid", "partial", "paid"}


def _validate_payment_status(v: str | None) -> str | None:
    if v is not None and v not in PAYMENT_STATUSES:
        raise ValueError(f"payment_status must be one of {sorted(PAYMENT_STATUSES)}")
    return v


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
    # The companion app's id for this project; with `source_app` it identifies the project, and creating it again returns the
    # existing one (BIZ-172). Requires `source_app`.
    external_ref: Optional[str] = Field(default=None, min_length=1, max_length=255)
    amount_paid: Optional[float] = None
    price: Optional[float] = None
    payment_status: str = "unpaid"
    # Staff/API-created projects default to "queued" (unchanged behavior); customer drafts
    # are created via the customer portal.
    stage: str = "queued"
    customer_id: Optional[int] = None

    @field_validator("payment_status")
    @classmethod
    def _valid_payment_status(cls, v: str) -> str:
        return _validate_payment_status(v)

    @field_validator("stage")
    @classmethod
    def _valid_stage(cls, v: str) -> str:
        if v not in PROJECT_STAGES:
            raise ValueError(f"stage must be one of {list(PROJECT_STAGES)}")
        return v


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    customer: Optional[str] = None
    order_type: Optional[str] = None
    on_hold: Optional[bool] = None
    due_date: Optional[str] = None
    notes: Optional[str] = None
    amount_paid: Optional[float] = None
    price: Optional[float] = None  # send null to clear
    price_visible: Optional[bool] = None  # show the quote (price, paid, balance) in the customer portal
    payment_status: Optional[str] = None
    customer_id: Optional[int] = None  # send null to unassign

    @field_validator("payment_status")
    @classmethod
    def _valid_payment_status(cls, v: Optional[str]) -> Optional[str]:
        return _validate_payment_status(v)


class ProjectShareOut(BaseModel):
    enabled: bool
    token: Optional[str] = None
    created_at: Optional[str] = None


class ProjectItemCreate(BaseModel):
    file_id: int
    quantity: int = 1
    filament_type: str = "any"
    filament_color: str = "any"
    filament_id: Optional[int] = None       # legacy filament id; or material_ref (+ material_provider)
    material_provider: Optional[str] = None
    material_ref: Optional[str] = None
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
    material_provider: Optional[str] = None
    material_ref: Optional[str] = None
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


class ProjectPartCreate(BaseModel):
    name: str
    quantity: int = 1
    allocated: bool = False
    sort_order: int = 0
    unit_cost: Optional[float] = Field(default=None, ge=0, le=1_000_000)

    @field_validator("quantity")
    @classmethod
    def qty_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("quantity must be at least 1")
        return v


class ProjectPartUpdate(BaseModel):
    name: Optional[str] = None
    quantity: Optional[int] = None
    allocated: Optional[bool] = None
    sort_order: Optional[int] = None
    unit_cost: Optional[float] = Field(default=None, ge=0, le=1_000_000)  # send null to clear

    @field_validator("quantity")
    @classmethod
    def qty_positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("quantity must be at least 1")
        return v


class GenerateRequest(BaseModel):
    eligible_printer_ids: list[int] = []
    # OrcaSlicer process/print preset name, resolved against each eligible
    # printer's process catalog. No default is invented when omitted — an
    # unset print_profile fails cleanly at slice time with an actionable error
    # (see slicer_service._resolve_uuids) rather than silently misresolving.
    process_preset: Optional[str] = None
    # Make/models ("any printer whose machine preset is X") eligible alongside the specific printers above.
    eligible_machine_profiles: list[str] = []
    # Slicing cache (BIZ-192): keep each generated job's production slice in the library as a cached version.
    save_slice: bool = False
    # Slicing cache (BIZ-193): reuse an identical earlier pack, and let each job print a matching cached version
    # instead of slicing when a printer claims it.
    allow_cached: bool = True


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
        "material_provider": item.material_provider,
        "material_ref": item.material_ref,
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


def _part_dict(part: ProjectPart) -> dict:
    return {
        "id": part.id,
        "project_id": part.project_id,
        "name": part.name,
        "quantity": part.quantity,
        "allocated": part.allocated,
        "sort_order": part.sort_order,
        "created_at": part.created_at,
        "unit_cost": part.unit_cost,
    }


async def _load_parts(session: AsyncSession, project_id: int) -> list[dict]:
    rows = (
        await session.execute(
            select(ProjectPart)
            .where(ProjectPart.project_id == project_id)
            .order_by(ProjectPart.sort_order, ProjectPart.id)
        )
    ).scalars().all()
    return [_part_dict(p) for p in rows]


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


_TERMINAL = {"complete", "failed", "cancelled"}


def _project_progress(job_rows: list[Job]) -> dict:
    jobs_total = len(job_rows)
    jobs_complete = sum(1 for j in job_rows if j.status == "complete")

    estimate_filament_grams_total = (
        sum(j.estimate_filament_grams for j in job_rows if j.estimate_filament_grams is not None) or None
    )
    estimate_seconds_total = (
        sum(j.estimate_seconds for j in job_rows if j.estimate_seconds is not None) or None
    )
    estimate_filament_grams_remaining = (
        sum(
            j.estimate_filament_grams for j in job_rows
            if j.estimate_filament_grams is not None and j.status not in _TERMINAL
        ) or None
    )
    estimate_seconds_remaining = (
        sum(
            j.estimate_seconds for j in job_rows
            if j.estimate_seconds is not None and j.status not in _TERMINAL
        ) or None
    )
    actual_filament_grams = (
        sum(j.actual_filament_grams for j in job_rows if j.actual_filament_grams is not None) or None
    )
    actual_seconds = (
        sum(j.actual_seconds for j in job_rows if j.actual_seconds is not None) or None
    )
    filament_cost_values = [j.filament_cost for j in job_rows if j.filament_cost is not None]
    filament_cost_total = round(sum(filament_cost_values), 2) if filament_cost_values else None

    return {
        "jobs_total": jobs_total,
        "jobs_complete": jobs_complete,
        "estimate_filament_grams_total": round(estimate_filament_grams_total, 2) if estimate_filament_grams_total else None,
        "estimate_seconds_total": estimate_seconds_total,
        "estimate_filament_grams_remaining": round(estimate_filament_grams_remaining, 2) if estimate_filament_grams_remaining else None,
        "estimate_seconds_remaining": estimate_seconds_remaining,
        "actual_filament_grams": round(actual_filament_grams, 2) if actual_filament_grams else None,
        "actual_seconds": actual_seconds,
        "filament_cost_total": filament_cost_total,
    }


async def _project_dict(project: Project, session: AsyncSession, costs: dict | None = None) -> dict:
    items = await _load_items(session, project.id)
    links = await _load_links(session, project.id)
    parts = await _load_parts(session, project.id)

    job_rows = (await session.execute(
        select(Job).where(Job.project_id == project.id)
    )).scalars().all()
    progress = _project_progress(job_rows)
    customer = await session.get(Customer, project.customer_id) if project.customer_id else None

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
        "external_ref": project.external_ref,
        "amount_paid": project.amount_paid,
        "price": project.price,
        "payment_status": project.payment_status,
        "price_visible": project.price_visible,
        "quote_accepted_at": project.quote_accepted_at,
        "stage": project.stage,
        "customer_id": project.customer_id,
        "customer_name": customer.name if customer else None,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "items": items,
        "links": links,
        "parts": parts,
        # Filament + machine + labour + parts (see services/job_costs.py); rates are the current settings.
        "costs": costs if costs is not None else
                 (await job_costs.costs_by_project(session, [project.id], {project.id: list(job_rows)}))[project.id],
        **progress,
    }


async def _valid_customer_id(customer_id: Optional[int], session: AsyncSession) -> Optional[int]:
    if customer_id is not None and await session.get(Customer, customer_id) is None:
        raise HTTPException(404, f"Customer {customer_id} not found")
    return customer_id


async def _get_project_or_404(project_id: int, session: AsyncSession) -> Project:
    proj = await session.get(Project, project_id)
    if proj is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return proj


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------

@router.get("", summary="List projects", dependencies=[Depends(require_scope("projects:read"))])
async def list_projects(
    source_app: Optional[str] = Query(None, description="Only projects created by this source application"),
    external_ref: Optional[str] = Query(None, description="Only the project with this external ref (use with source_app)"),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """All projects ordered by creation date descending, each with items, links,
    job counts, and aggregated filament/time estimates. `?source_app=&external_ref=` looks a project up by the
    companion app's own key."""
    q = select(Project).order_by(Project.created_at.desc())
    if source_app is not None:
        q = q.where(Project.source_app == source_app)
    if external_ref is not None:
        q = q.where(Project.external_ref == external_ref)
    rows = (await session.execute(q)).scalars().all()
    # Costs for every listed project in a constant number of queries (not 4 per project).
    ids = [p.id for p in rows]
    jobs_by_project: dict[int, list[Job]] = {i: [] for i in ids}
    if ids:
        for j in (await session.execute(select(Job).where(Job.project_id.in_(ids)))).scalars().all():
            jobs_by_project[j.project_id].append(j)
    costs = await job_costs.costs_by_project(session, ids, jobs_by_project)
    return [await _project_dict(p, session, costs[p.id]) for p in rows]


async def _remember(factory: async_sessionmaker[AsyncSession], scope: str, key: str, status_code: int, result: dict) -> None:
    """Store the response under the key. The work is already committed, so a failure here is logged, not raised."""
    try:
        await idempotency.complete(factory, scope, key, status_code, result)
    except Exception:
        logger.exception("Could not store the idempotent response for %s", scope)


async def _job_count(factory: async_sessionmaker[AsyncSession], project_id: int) -> int:
    async with factory() as s:
        return (await s.execute(select(func.count()).select_from(Job).where(Job.project_id == project_id))).scalar_one()


async def _find_by_external_ref(session: AsyncSession, source_app: str, external_ref: str) -> Project | None:
    return (await session.execute(select(Project).where(Project.source_app == source_app, Project.external_ref == external_ref))).scalar_one_or_none()


async def _create_project(body: ProjectCreate, session: AsyncSession, progress: dict) -> tuple[dict, bool]:
    """(project dict, created). With an external ref that already exists, nothing is created and the existing project is returned."""
    if body.external_ref is not None:
        existing = await _find_by_external_ref(session, body.source_app or "", body.external_ref)
        if existing is not None:
            return await _project_dict(existing, session), False
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
        external_ref=body.external_ref,
        amount_paid=body.amount_paid,
        price=body.price,
        payment_status=body.payment_status,
        stage=body.stage,
        customer_id=await _valid_customer_id(body.customer_id, session),
        created_at=now,
        updated_at=now,
    )
    session.add(proj)
    try:
        await session.flush()
    except IntegrityError:
        # A concurrent request with the same (source_app, external_ref) won the unique index: return its project.
        await session.rollback()
        existing = await _find_by_external_ref(session, body.source_app or "", body.external_ref or "")
        if existing is None:
            raise
        return await _project_dict(existing, session), False
    if proj.amount_paid and proj.amount_paid > 0:
        # Record what was entered as a real payment so amount paid stays derived from payment rows.
        await adopt_manual_amount(session, proj)
        await sync_project_totals(session, proj)
    await session.commit()
    progress["created_id"] = proj.id                       # from here on a failure must not be retried under the same Idempotency-Key
    await session.refresh(proj)
    await project_events.publish("project.created", proj)
    return await _project_dict(proj, session), True


@router.post("", status_code=201, summary="Create project",
             responses={200: {"description": "A project with this source_app + external_ref already exists: returned unchanged"},
                        409: {"description": "Another request with this Idempotency-Key is still running"},
                        422: {"description": "external_ref without source_app, or an Idempotency-Key reused for a different request"}},
             dependencies=[Depends(require_scope("projects:write"))])
async def create_project(
    body: ProjectCreate,
    response: Response,
    session: AsyncSession = Depends(get_session),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key", description="Repeat-safe creation: a retry returns the first response"),
) -> dict:
    """Create a project. Retry-safe for companion apps two ways: give it a `source_app` + `external_ref` (a second create with the same
    pair returns the existing project, status 200, unchanged) and/or an `Idempotency-Key` header (a repeat returns the first response with
    `Idempotent-Replay: true`)."""
    if body.external_ref is not None and not body.source_app:
        raise HTTPException(422, "external_ref needs a source_app")
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    scope = "POST /api/v1/projects"
    if idempotency_key:
        replay = await idempotency.claim(factory, scope, idempotency_key, idempotency.request_hash(body.model_dump()))
        if replay is not None:
            response.status_code, response.headers["Idempotent-Replay"] = replay.status_code, "true"
            return replay.response
    progress: dict = {}
    try:
        result, created = await _create_project(body, session, progress)
    except BaseException:
        if idempotency_key:
            if progress.get("created_id") is not None:
                await idempotency.partial(factory, scope, idempotency_key, f"Project {progress['created_id']} was already created by this "
                                          "request before it failed: fetch it instead of retrying with this key")
            else:
                await idempotency.release(factory, scope, idempotency_key)
        raise
    response.status_code = 201 if created else 200
    if idempotency_key:
        await _remember(factory, scope, idempotency_key, response.status_code, result)
    return result


@router.get(
    "/{project_id}",
    summary="Get project",
    responses={
        404: {"description": "Project not found"},
    },
    dependencies=[Depends(require_scope("projects:read"))],
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
    dependencies=[Depends(require_scope("projects:write"))],
)
async def patch_project(
    project_id: int,
    body: ProjectPatch,
    session: AsyncSession = Depends(get_session),
) -> dict:
    proj = await _get_project_or_404(project_id, session)
    changes_derived = (
        (body.amount_paid is not None and round(body.amount_paid, 2) != round(proj.amount_paid or 0.0, 2))
        or (body.payment_status is not None and body.payment_status != proj.payment_status)
    )
    # Echoing the current values back (a client PATCHing the whole object) is fine; changing them is not.
    if changes_derived and await has_payments(session, project_id):
        raise HTTPException(
            409, "This project's amount paid and payment status come from its recorded payments — "
                 "add, edit or delete a payment instead")
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
    if body.amount_paid is not None and not await has_payments(session, project_id):
        proj.amount_paid = body.amount_paid
    if "price" in body.model_fields_set:
        if body.price != proj.price:
            proj.quote_accepted_at = None   # what the customer accepted no longer matches
        proj.price = body.price
    if body.price_visible is not None:
        proj.price_visible = body.price_visible
    if body.payment_status is not None and not await has_payments(session, project_id):
        proj.payment_status = body.payment_status
    if "customer_id" in body.model_fields_set:
        proj.customer_id = await _valid_customer_id(body.customer_id, session)
    if "price" in body.model_fields_set:
        await sync_project_totals(session, proj)  # paid/partial depends on the quoted price
    proj.updated_at = _now_iso()
    await session.commit()
    await session.refresh(proj)
    return await _project_dict(proj, session)


class ProjectPromote(BaseModel):
    stage: str


@router.post(
    "/{project_id}/promote",
    summary="Promote project stage",
    responses={
        404: {"description": "Project not found"},
        409: {"description": "Not a forward transition (draft → planning → queued)"},
    },
    dependencies=[Depends(require_scope("projects:write"))],
)
async def promote_project(
    project_id: int,
    body: ProjectPromote,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Move a project forward one stage: draft → planning (jobs may be created) →
    queued (its jobs become eligible for printers)."""
    proj = await _get_project_or_404(project_id, session)
    current = PROJECT_STAGES.index(proj.stage) if proj.stage in PROJECT_STAGES else -1
    if body.stage not in PROJECT_STAGES or PROJECT_STAGES.index(body.stage) != current + 1:
        raise HTTPException(409, f"Cannot move project from {proj.stage!r} to {body.stage!r}")
    previous_stage = proj.stage
    proj.stage = body.stage
    proj.updated_at = _now_iso()
    await session.commit()
    await session.refresh(proj)
    await project_events.publish("project.stage_changed", proj, previous_stage=previous_stage)
    if proj.stage == "queued":
        queue_engine.wake()
    return await _project_dict(proj, session)


@router.delete(
    "/{project_id}",
    summary="Delete project",
    responses={
        404: {"description": "Project not found"},
    },
    dependencies=[Depends(require_scope("projects:write"))],
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
    "/{project_id}/share",
    response_model=ProjectShareOut,
    summary="Get project share-link state",
    responses={404: {"description": "Project not found"}},
    dependencies=[Depends(require_scope("projects:share"))],
)
async def get_project_share(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> ProjectShareOut:
    proj = await _get_project_or_404(project_id, session)
    return ProjectShareOut(
        enabled=proj.share_token is not None, token=proj.share_token,
        created_at=proj.share_token_created_at,
    )


@router.put(
    "/{project_id}/share",
    response_model=ProjectShareOut,
    summary="Create or regenerate the project's share link",
    responses={404: {"description": "Project not found"}},
    dependencies=[Depends(require_scope("projects:share"))],
)
async def put_project_share(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> ProjectShareOut:
    """Always generates a fresh token, whether or not one already existed - "create"
    and "regenerate" are the same operation. Retries once on the astronomically
    unlikely event of a token collision (unique constraint violation)."""
    proj = await _get_project_or_404(project_id, session)
    for attempt in range(2):
        proj.share_token = secrets.token_urlsafe(32)
        proj.share_token_created_at = _now_iso()
        try:
            await session.commit()
            break
        except IntegrityError:
            await session.rollback()
            if attempt == 1:
                raise
    await session.refresh(proj)
    return ProjectShareOut(enabled=True, token=proj.share_token, created_at=proj.share_token_created_at)


@router.delete(
    "/{project_id}/share",
    response_model=ProjectShareOut,
    summary="Revoke the project's share link",
    responses={404: {"description": "Project not found"}},
    dependencies=[Depends(require_scope("projects:share"))],
)
async def delete_project_share(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> ProjectShareOut:
    proj = await _get_project_or_404(project_id, session)
    proj.share_token = None
    proj.share_token_created_at = None
    await session.commit()
    await session.refresh(proj)
    return ProjectShareOut(enabled=False, token=None)


@router.get(
    "/{project_id}/jobs",
    summary="List project jobs",
    responses={
        404: {"description": "Project not found"},
    },
    dependencies=[Depends(require_scope("projects:read"))],
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
    if is_presliced_name(f.original_filename):   # project items get packed and sliced: gcode can't be
        raise HTTPException(422, f"{f.original_filename} is already sliced — add the model it was sliced from")
    item = ProjectItem(
        project_id=project_id,
        file_id=body.file_id,
        quantity=body.quantity,
        filament_type=body.filament_type,
        filament_color=body.filament_color,
        **material_columns(body.filament_id, body.material_provider, body.material_ref),
        sort_order=body.sort_order,
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
    if body.filament_type is not None:
        item.filament_type = body.filament_type
    if body.filament_color is not None:
        item.filament_color = body.filament_color
    if body.filament_id is not None or body.material_ref is not None:
        # a lone material_provider means nothing without a ref; an old client's echo of the stored ref must not undo its filament_id edit
        for column, value in material_columns(body.filament_id, body.material_provider, body.material_ref, stored(item)).items():
            setattr(item, column, value)
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


async def _reusable_pack(session: AsyncSession, library_dir: Path, recipe_hash: str) -> UploadedFile | None:
    """The newest earlier pack made from the same recipe whose file is still on disk, or None."""
    rows = (await session.execute(
        select(UploadedFile).where(UploadedFile.pack_recipe_hash == recipe_hash, UploadedFile.missing.is_(False))
        .order_by(UploadedFile.id.desc())
    )).scalars().all()
    for f in rows:
        if not f.content_hash:
            continue
        fresh = await asyncio.to_thread(
            fresh_content_hash, library_abs_path(library_dir, f.relative_path), f.content_hash, f.size_bytes, f.mtime)
        if fresh is not None and fresh[0] == f.content_hash:   # present and not edited since it was packed
            return f
    return None


def _item_material(item: ProjectItem) -> tuple[str, str] | None:
    """The item's specific-material ask as (provider, ref); a pre-migration row only has the legacy filament_id."""
    if item.material_ref:
        return (item.material_provider or inventory_refs.LEGACY_PROVIDER, item.material_ref)
    return (inventory_refs.LEGACY_PROVIDER, str(item.filament_id)) if item.filament_id is not None else None


def _material_cols(mat: tuple[str, str] | None) -> dict:
    return material_columns(None, *mat) if mat else {"filament_id": None, "material_provider": None, "material_ref": None}


def _filament_label(fil_type: str, fil_color: str, mat: tuple[str, str] | None) -> str:
    """Short string for use in generated 3MF filenames."""
    if mat is not None:
        return f"f{mat[1]}" if mat[0] == inventory_refs.LEGACY_PROVIDER else f"{mat[0]}-{mat[1]}"
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
        409: {"description": "Project is a draft, or another request with this Idempotency-Key is still running"},
    },
    dependencies=[Depends(require_scope("projects:write"))],
)
async def generate_project(
    project_id: int,
    body: GenerateRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    session: AsyncSession = Depends(get_session),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key", description="Repeat-safe generation: a retry returns the first result"),
) -> dict:
    """Pack project STL items into 3MF files (one per filament group), save them to the library,
    and queue one job per plate. Returns created job and file IDs plus the bed dimensions used
    for packing.
    Send an `Idempotency-Key` header to make the call safe to retry: a repeat with the same key and body returns the first result
    (`Idempotent-Replay: true`) and creates no further files or jobs."""
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    scope = f"POST /api/v1/projects/{project_id}/generate"
    if idempotency_key:
        replay = await idempotency.claim(factory, scope, idempotency_key, idempotency.request_hash(project_id, body.model_dump()))
        if replay is not None:
            response.status_code, response.headers["Idempotent-Replay"] = replay.status_code, "true"
            return replay.response
    jobs_before = await _job_count(factory, project_id) if idempotency_key else 0
    try:
        result = await _generate_project(project_id, body, background_tasks, session)
    except BaseException:
        if idempotency_key:
            created = await _job_count(factory, project_id) - jobs_before
            if created > 0:       # some groups were committed: a retry under this key would generate them again
                await idempotency.partial(factory, scope, idempotency_key, f"This request created {created} job(s) for project {project_id} "
                                          "before it failed: check the project's jobs, then retry with a new Idempotency-Key")
            else:
                await idempotency.release(factory, scope, idempotency_key)
        raise
    if idempotency_key:
        await _remember(factory, scope, idempotency_key, 200, result)
    return result


async def _generate_project(project_id: int, body: GenerateRequest, background_tasks: BackgroundTasks, session: AsyncSession) -> dict:
    proj = await _get_project_or_404(project_id, session)
    if proj.stage == "draft":
        raise HTTPException(409, "Promote the project to planning before creating jobs")

    slicing = get_slicing_provider()
    if slicing is None:
        raise HTTPException(422, "LAMINUS_SIDECAR_URL is not configured — Laminus sidecar required for generation")
    if not slicing.PACK_MODELS:
        raise HTTPException(422, "The slicing provider cannot pack models — project generation is unavailable")

    # Resolve eligible printers and compute the smallest bed dimensions.
    eligible_printers: list[Printer] = []
    if body.eligible_printer_ids:
        rows = (await session.execute(
            select(Printer).where(Printer.id.in_(body.eligible_printer_ids))
        )).scalars().all()
        eligible_printers = list(rows)
    model_printers: list[Printer] = []
    if body.eligible_machine_profiles:
        model_printers = list((await session.execute(
            select(Printer).where(Printer.current_orca_printer_profile.in_(body.eligible_machine_profiles))
        )).scalars().all())

    # Pack for the smallest bed among everything that could take the job (explicit picks + matching models).
    bed_printers = eligible_printers + [p for p in model_printers if p.id not in {e.id for e in eligible_printers}]
    if bed_printers:
        pack_bed_x = min(p.bed_x_mm for p in bed_printers)
        pack_bed_y = min(p.bed_y_mm for p in bed_printers)
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

    # Group items by filament requirement (type, color, specific material (provider, ref))
    groups: dict[tuple[str, str, tuple[str, str] | None], list[ProjectItem]] = {}
    for item in item_rows:
        key = (item.filament_type, item.filament_color, _item_material(item))
        groups.setdefault(key, []).append(item)

    library_dir = get_library_dir()

    # Resolve STL paths per group (+ what each group packs from, for pack reuse)
    group_paths: dict[tuple[str, str, tuple[str, str] | None], list[Path]] = {}
    group_recipe: dict[tuple[str, str, tuple[str, str] | None], list[tuple[str, int]]] = {}
    for key, group_items in groups.items():
        paths: list[Path] = []
        group_recipe[key] = []
        for item in group_items:
            f = await session.get(UploadedFile, item.file_id)
            if f is None:
                raise HTTPException(422, f"File {item.file_id} not found in library")
            if not f.original_filename.lower().endswith(".stl"):
                raise HTTPException(400, f"File {f.original_filename!r} is not an STL — only STL files are supported")
            stl_path = library_abs_path(library_dir, f.relative_path)
            if not stl_path.exists():
                raise HTTPException(422, f"STL file {f.original_filename!r} is missing from disk")
            paths.extend([stl_path] * item.quantity)
            # The recipe keys on each STL's bytes: re-hash one that changed on disk since the last rescan.
            await refresh_content_hash(f, library_dir)
            group_recipe[key].append((f.content_hash or f"file:{f.id}:{f.size_bytes}:{f.mtime}", item.quantity))
        group_paths[key] = paths
    pack_mode = ({"mode": "uuid", "machine": proj.machine_uuid, "process": proj.process_uuid}
                 if proj.machine_uuid and proj.process_uuid else {"mode": "geometry", "bed": [pack_bed_x, pack_bed_y]})

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
            # Never delete a file the slicing cache still relies on (a reusable pack, or one with cached slices).
            if old_file and (old_file.pack_recipe_hash or (await session.execute(
                    select(SlicedVersion.id).where(SlicedVersion.source_file_id == old_file.id).limit(1))).first()):
                old_file = None
            if old_file:
                old_abs = library_abs_path(library_dir, old_file.relative_path)
                if old_abs.exists():
                    old_abs.unlink(missing_ok=True)
                await session.delete(old_file)
        proj.result_file_id = None
        await session.commit()

    job_pack_dir = library_dir / "Job Pack 3MFs"
    job_pack_dir.mkdir(parents=True, exist_ok=True)

    jobs_out: list[dict] = []
    files_out: list[dict] = []

    for (fil_type, fil_color, mat), stl_paths in group_paths.items():
        group_items = groups[(fil_type, fil_color, mat)]
        # Same STLs x quantities, same bed / pack mode => the same pack: reuse it rather than re-pack, so the cached
        # slices keyed on that file still apply (BIZ-193).
        recipe_hash = slice_cache.sha256_of({"items": group_recipe[(fil_type, fil_color, mat)], "pack": pack_mode})
        reused = await _reusable_pack(session, library_dir, recipe_hash) if body.allow_cached else None
        now = _now_iso()

        if reused is not None:
            new_file = reused
            plate_nums = [p.get("plate_number") for p in (reused.plates or []) if p.get("plate_number") is not None]
            slice_cache.log_event("pack_reused", project_id=proj.id, recipe_hash=recipe_hash, pack_file_id=reused.id,
                                  pack_content_hash=reused.content_hash)
        else:
            try:
                if proj.machine_uuid and proj.process_uuid:
                    # Legacy path: project has OrcaSlicer profiles embedded
                    packed_bytes = await asyncio.to_thread(
                        lambda: slicing.pack_models(
                            stl_paths, machine_ref=proj.machine_uuid, process_ref=proj.process_uuid,
                            filament_refs=[]),
                    )
                else:
                    # Geometry-only pack; slicing profiles applied at dispatch time
                    packed_bytes = await asyncio.to_thread(
                        lambda: slicing.pack_models(stl_paths, bed=(pack_bed_x, pack_bed_y, 250.0)),
                    )
            except SlicingProviderError as exc:
                if "timed out" in str(exc).lower():
                    raise HTTPException(504, "Generation timed out — try fewer parts or reduce quantities")
                raise HTTPException(502, f"Orca sidecar error during generation: {exc}") from exc

            label = _filament_label(fil_type, fil_color, mat)
            out_filename = f"project-{_slugify(proj.name)}-{label}.3mf"
            # Write to a temp subdirectory; renamed to the job-ID subfolder once IDs are known.
            tmp_subdir = job_pack_dir / f"_tmp_{_uuid.uuid4().hex[:10]}"
            tmp_subdir.mkdir(parents=True, exist_ok=True)
            out_path = tmp_subdir / out_filename
            out_path.write_bytes(packed_bytes)
            # A real content hash: the slicing cache keys versions on it (an empty one is uncacheable), and the
            # library's dedup/move detection relies on it too.
            pack_hash = hashlib.sha256(packed_bytes).hexdigest()

            plate_nums = _parse_plate_nums(out_path)

            rel = out_path.relative_to(library_dir).as_posix()
            new_file = UploadedFile(
                original_filename=out_path.name,
                plates=[{"plate_number": p, "thumbnail_path": None} for p in plate_nums],
                uploaded_at=now,
                relative_path=rel,
                folder="/Job Pack 3MFs",
                size_bytes=out_path.stat().st_size,
                content_hash=pack_hash,
                mtime=out_path.stat().st_mtime,
                missing=False,
                pack_recipe_hash=recipe_hash,
            )
            session.add(new_file)
            await session.commit()
            await session.refresh(new_file)
            slice_cache.log_event("pack_new", project_id=proj.id, recipe_hash=recipe_hash, pack_file_id=new_file.id,
                                  pack_content_hash=pack_hash)

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
                save_slice=body.save_slice,
                allow_cached_slice=body.allow_cached,
            )
            session.add(job)
            new_jobs.append(job)
            next_pos += 1.0
        # Commit jobs so the DB assigns their autoincrement IDs.
        await session.commit()
        # Refresh each job so j.id is populated.
        for j in new_jobs:
            await session.refresh(j)

        if reused is None:
            # Rename temp dir to the job-ID subfolder now that IDs are known.
            job_id_label = str(new_jobs[0].id) if len(new_jobs) == 1 else f"{new_jobs[0].id}-{new_jobs[-1].id}"
            final_subdir = job_pack_dir / job_id_label
            tmp_subdir.rename(final_subdir)
            final_path = final_subdir / out_filename
            new_file.relative_path = final_path.relative_to(library_dir).as_posix()
            new_file.folder = f"/Job Pack 3MFs/{job_id_label}"
            await session.commit()

            background_tasks.add_task(regen_file_thumbnails, new_file.id)

        cached_plates = (await session.execute(
            select(func.count(func.distinct(SlicedVersion.plate_number)))
            .join(UploadedFile, UploadedFile.id == SlicedVersion.file_id)
            .where(SlicedVersion.source_file_id == new_file.id, UploadedFile.missing.is_(False))
        )).scalar() or 0
        files_out.append({
            "id": new_file.id,
            "original_filename": new_file.original_filename,
            "folder": new_file.folder,
            "plate_count": len(plate_nums),
            # Slicing cache (informational — whether a version is used is decided when a printer claims each job).
            "pack_reused": reused is not None,
            "cached_plates": cached_plates,
        })

        # Create printer configs for each eligible printer × job. The machine preset
        # is deliberately NOT set here — queue_engine reads it fresh from the printer
        # at dispatch time. print_profile is the *process* preset and must come from
        # the user (body.process_preset); current_orca_printer_profile is a machine
        # preset name and can never resolve against the process catalog.
        for j in new_jobs:
            for p in eligible_printers:
                session.add(JobPrinterConfig(
                    job_id=j.id,
                    printer_id=p.id,
                    print_profile=body.process_preset or "",
                    filament_type=fil_type,
                    filament_color=fil_color,
                    **_material_cols(mat),
                ))
            for machine_profile in dict.fromkeys(body.eligible_machine_profiles):
                session.add(JobModelTarget(
                    job_id=j.id,
                    machine_profile=machine_profile,
                    print_profile=body.process_preset or "",
                    filament_profile=None,   # the loaded slot supplies the preset on each matching printer
                    filament_type=fil_type,
                    filament_color=fil_color,
                    **_material_cols(mat),
                ))
            await session.flush()
            await model_targets.materialize_job(session, j.id)
        await session.commit()

        # Trigger estimates for each new job if enabled.
        queue_cfg = await session.get(QueueConfig, 1)
        est_enabled = queue_cfg is not None and queue_cfg.estimates_enabled
        if est_enabled:
            for j in new_jobs:
                j.estimate_token = (j.estimate_token or 0) + 1
                j.estimate_status = "pending"
            await session.commit()
            for j in new_jobs:
                queue_engine.spawn_estimate(j.id)

        jobs_out.extend({
            "id": j.id,
            "uploaded_file_id": j.uploaded_file_id,
            "plate_number": j.plate_number,
            "queue_position": j.queue_position,
            "status": j.status,
        } for j in new_jobs)

    proj.updated_at = _now_iso()
    await session.commit()
    if jobs_out:
        await project_events.publish("project.generated", proj, job_ids=[j["id"] for j in jobs_out])

    return {
        "project_id": proj.id,
        "jobs": jobs_out,
        "files": files_out,
        "eligible_printer_ids": [p.id for p in eligible_printers],
        "pack_bed_x": pack_bed_x,
        "pack_bed_y": pack_bed_y,
    }
