from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ._filament_ask import AskFields, ask_out
from ...auth import require_scope
from ...database import get_session
from ...models import Customer, Job, PROJECT_STAGES, Project, ProjectGroup, ProjectItem, ProjectLink, ProjectPart, UploadedFile
from ...services import idempotency, job_costs, project_events, project_groups
from ...services.payments import adopt_manual_amount, has_payments, sync_project_totals
from ...services.queue_engine import queue_engine

from ...services.providers.construction import get_project_constructor
from ...services.library_store import get_library_store
from ...services.library_scanner import is_presliced_name, refresh_content_hash

# Collaborators project_children.py / project_generate.py call as `_projects.<name>` rather than importing themselves:
# tests swap them out on THIS module (`patch("app.api.routes.projects.get_project_constructor")`).
_PATCH_POINTS = (get_project_constructor, queue_engine, get_library_store, refresh_content_hash, secrets)

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


ItemKind = Literal["stl", "3mf_object", "3mf_plate", "gcode"]
PackRule = Literal["split", "together", "solo", "locked"]
# Which packing rules each kind can use (BIZ-356); the first is the default. gcode prints as sliced.
_RULES: dict[str, tuple[str, ...]] = {"stl": ("split", "solo"), "3mf_object": ("split", "solo"),
                                     "3mf_plate": ("split", "together", "solo", "locked"), "gcode": ("locked",)}


class ProjectItemCreate(AskFields):
    file_id: int
    quantity: int = 1
    sort_order: int = 0
    # BIZ-356: what of the file this item is. Omitted = inferred from the file: an STL; a .gcode / .gcode.3mf (plate
    # `source_ref`, default 1); a .3mf's plate `source_ref` (default 1). `source_ref` = object id for "3mf_object".
    kind: Optional[ItemKind] = None
    source_ref: Optional[str] = Field(default=None, max_length=64)
    pack_rule: Optional[PackRule] = None
    group_id: Optional[int] = None          # BIZ-363: a print group of this project (default: the Default Group)

    @field_validator("quantity")
    @classmethod
    def qty_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("quantity must be at least 1")
        return v


class ProjectItemUpdate(BaseModel):
    """A PATCH: only the fields sent change. The resulting ask must still be valid (a type needs a subtype)."""
    quantity: Optional[int] = None
    filament_manufacturer: Optional[str] = None
    filament_type: Optional[str] = None
    filament_subtype: Optional[str] = None
    filament_color: Optional[str] = None
    sort_order: Optional[int] = None
    group_id: Optional[int] = None          # move the item to another print group of the same project (BIZ-360)
    pack_rule: Optional[PackRule] = None    # BIZ-356: must be one its kind allows

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
    # printer's process catalog. Omitted = each printer's own default process profile (BIZ-358).
    process_preset: Optional[str] = None
    # Printer models ("any printer of model X", registry UUIDs, BIZ-294) eligible alongside the specific printers above.
    eligible_model_uuids: list[str] = []
    # Any printer at all, including ones added later (BIZ-357): an "Any printer" model target on every job.
    eligible_any_printer: bool = False
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
        **ask_out(item),
        "sort_order": item.sort_order,
        "group_id": item.group_id,
        "quantity_held": item.quantity_held or 0,
        "kind": item.kind or "stl",
        "source_ref": item.source_ref,
        "pack_rule": item.pack_rule or "split",
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
    await project_groups.ensure_default_group(session, proj.id)      # every project has its Default Group (BIZ-360)
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


# Shared by the item routes (project_children.py) and generation (project_generate.py).
async def _item_group(session: AsyncSession, project_id: int, group_id: int | None) -> int:
    """The group a new item joins: `group_id` when it is a print group of this project (422 otherwise), else the Default Group."""
    if group_id is None:
        return (await project_groups.ensure_default_group(session, project_id)).id
    group = await session.get(ProjectGroup, group_id)
    if group is None or group.project_id != project_id:
        raise HTTPException(422, f"Group {group_id} is not a print group of project {project_id}")
    return group.id


def _item_kind(f: UploadedFile, kind: str | None, ref: str | None, rule: str | None) -> tuple[str, str | None, str]:
    """Validate (and infer) what of `f` an item is (BIZ-356): (kind, source_ref, pack_rule), else 422."""
    name = f.original_filename.lower()
    natural = "gcode" if is_presliced_name(f.original_filename) else "stl" if name.endswith(".stl") else "3mf_plate"
    kind = kind or natural
    if kind == "gcode" and natural != "gcode" or kind == "stl" and natural != "stl" or \
            kind in ("3mf_object", "3mf_plate") and natural != "3mf_plate":
        raise HTTPException(422, f"{f.original_filename} can't be a {kind} item")
    if kind == "3mf_object" and not ref:
        raise HTTPException(422, "A 3MF object item needs source_ref: the object id")
    if kind in ("3mf_plate", "gcode"):
        ref = "1" if name.endswith(".gcode") else (ref or "1")          # a raw .gcode is one plate
        plates = {p.get("plate_number") for p in (f.plates or []) if p.get("plate_number") is not None}
        if not (ref.isascii() and ref.isdigit() and int(ref) >= 1) or (plates and int(ref) not in plates):
            raise HTTPException(422, f"{f.original_filename} has no plate {ref}" + (f" (plates {sorted(plates)})" if plates else ""))
    if kind == "stl":
        ref = None
    allowed = _RULES[kind]
    rule = rule or allowed[0]
    if rule not in allowed:
        raise HTTPException(422, f"A {kind} item can't use the {rule!r} rule (allowed: {', '.join(allowed)})")
    return kind, ref, rule


# ---- the rest of the projects routes ---------------------------------------------------------------
# Imported last: each module below registers its routes on `router` (so they match after the ones above, in this order)
# and uses the names defined above. Their names are re-exported here for existing importers.
from .project_children import (  # noqa: E402,F401,I001
    add_item,  # noqa: F401
    add_link,  # noqa: F401
    add_part,  # noqa: F401
    delete_item,  # noqa: F401
    delete_link,  # noqa: F401
    delete_part,  # noqa: F401
    list_items,  # noqa: F401
    list_links,  # noqa: F401
    list_parts,  # noqa: F401
    reorder_items,  # noqa: F401
    update_item,  # noqa: F401
    update_link,  # noqa: F401
    update_part,  # noqa: F401
)
from .project_generate import (  # noqa: E402,F401,I001
    HeldAction,  # noqa: F401
    PACK_FOLDER,  # noqa: F401
    _construct_http_error,  # noqa: F401
    _construct_in_thread,  # noqa: F401
    _construct_stored_models,  # noqa: F401
    _filament_label,  # noqa: F401
    _gcode_targets,  # noqa: F401
    _generate_project,  # noqa: F401
    _max_queue_position,  # noqa: F401
    _parse_plate_nums,  # noqa: F401
    _plan_group_packs,  # noqa: F401
    _plate_item_quantities,  # noqa: F401
    _queue_packs,  # noqa: F401
    _require_ready,  # noqa: F401
    _reusable_pack,  # noqa: F401
    _round_robin_quantities,  # noqa: F401
    _stored,  # noqa: F401
    _stored_manifest,  # noqa: F401
    _stored_plate_nums,  # noqa: F401
    generate_project,  # noqa: F401
    project_failures,  # noqa: F401
    project_review,  # noqa: F401
    resolve_held_parts,  # noqa: F401
)
