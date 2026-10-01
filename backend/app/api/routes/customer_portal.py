"""Customer portal: a logged-in customer sees only their own projects and those projects'
jobs, and may create/edit/upload to their own *draft* projects. Every query is filtered by
the session's customer_id (require_customer) — never by a client-supplied id."""
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_customer
from ...database import get_session
from ...models import Job, Project, ProjectItem, ProjectPayment, UploadedFile
from ...services.payments import outstanding, paid_amount
from .files import upload_file

router = APIRouter(prefix="/api/v1/customer", tags=["customer"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_dict(j: Job) -> dict:
    # Deliberately narrow: no printer, gcode, cost, or block-reason internals.
    return {
        "id": j.id, "status": j.status, "plate_number": j.plate_number,
        "created_at": j.created_at, "completed_at": j.completed_at,
        "estimate_seconds": j.estimate_seconds,
    }


async def _quote(p: Project, session: AsyncSession) -> dict | None:
    """What the customer may see about money — only once staff have made the price visible, and never anything
    internal (costs, profit, filament spend, payment notes). None = show nothing money-related."""
    if not p.price_visible or p.price is None:
        return None
    pays = (await session.execute(
        select(ProjectPayment).where(ProjectPayment.project_id == p.id)
        .order_by(ProjectPayment.received_on.desc(), ProjectPayment.id.desc())
    )).scalars().all()
    return {
        "price": p.price,
        "paid": round(paid_amount(p), 2),
        "balance": round(outstanding(p), 2),
        "accepted_at": p.quote_accepted_at,
        "payments": [{"id": x.id, "received_on": x.received_on, "amount": x.amount, "method": x.method} for x in pays],
    }


async def _project_dict(p: Project, session: AsyncSession) -> dict:
    jobs = (await session.execute(
        select(Job).where(Job.project_id == p.id).order_by(Job.id)
    )).scalars().all()
    items = (await session.execute(
        select(ProjectItem, UploadedFile)
        .join(UploadedFile, UploadedFile.id == ProjectItem.file_id)
        .where(ProjectItem.project_id == p.id)
        .order_by(ProjectItem.sort_order, ProjectItem.id)
    )).all()
    return {
        "id": p.id, "name": p.name, "notes": p.notes, "stage": p.stage, "due_date": p.due_date,
        "created_at": p.created_at, "updated_at": p.updated_at,
        "items": [{"id": i.id, "filename": f.original_filename, "quantity": i.quantity} for i, f in items],
        "jobs": [_job_dict(j) for j in jobs],
        "jobs_total": len(jobs),
        "jobs_complete": sum(1 for j in jobs if j.status == "complete"),
        "quote": await _quote(p, session),
    }


async def _own_project(project_id: int, customer_id: int, session: AsyncSession) -> Project:
    p = await session.get(Project, project_id)
    if p is None or p.customer_id != customer_id:
        raise HTTPException(404, "Project not found")
    return p


def _require_draft(p: Project) -> None:
    if p.stage != "draft":
        raise HTTPException(409, "Only draft projects can be edited")


class DraftCreate(BaseModel):
    name: str
    notes: str | None = None


class DraftPatch(BaseModel):
    name: str | None = None
    notes: str | None = None


@router.get("/projects", summary="List my projects")
async def list_my_projects(customer_id: int = Depends(require_customer),
                           session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await session.execute(
        select(Project).where(Project.customer_id == customer_id).order_by(Project.created_at.desc())
    )).scalars().all()
    return [await _project_dict(p, session) for p in rows]


@router.get("/projects/{project_id}", summary="Get my project")
async def get_my_project(project_id: int, customer_id: int = Depends(require_customer),
                         session: AsyncSession = Depends(get_session)) -> dict:
    return await _project_dict(await _own_project(project_id, customer_id, session), session)


@router.post("/projects/{project_id}/quote/accept", summary="Accept the quote")
async def accept_quote(project_id: int, customer_id: int = Depends(require_customer),
                       session: AsyncSession = Depends(get_session)) -> dict:
    """Records that the customer agreed to the quote they can see, and moves a draft request on to planning.
    Idempotent: accepting again changes nothing. 409 when there is no visible quote to accept."""
    p = await _own_project(project_id, customer_id, session)
    if not p.price_visible or p.price is None:
        raise HTTPException(409, "There is no quote to accept yet")
    if p.quote_accepted_at is None:
        p.quote_accepted_at = _now()
        if p.stage == "draft":
            p.stage = "planning"
        p.updated_at = _now()
        await session.commit()
    return await _project_dict(p, session)


@router.post("/projects", status_code=201, summary="Create draft project")
async def create_draft(body: DraftCreate, customer_id: int = Depends(require_customer),
                       session: AsyncSession = Depends(get_session)) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "name is required")
    now = _now()
    p = Project(name=name, notes=body.notes, customer="", order_type="customer", stage="draft",
                customer_id=customer_id, created_at=now, updated_at=now)
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return await _project_dict(p, session)


@router.patch("/projects/{project_id}", summary="Edit draft project")
async def update_draft(project_id: int, body: DraftPatch, customer_id: int = Depends(require_customer),
                       session: AsyncSession = Depends(get_session)) -> dict:
    p = await _own_project(project_id, customer_id, session)
    _require_draft(p)
    if body.name is not None and body.name.strip():
        p.name = body.name.strip()
    if body.notes is not None:
        p.notes = body.notes
    p.updated_at = _now()
    await session.commit()
    return await _project_dict(p, session)


@router.post("/projects/{project_id}/files", status_code=201, summary="Upload file to draft project")
async def upload_to_draft(project_id: int, file: UploadFile, background_tasks: BackgroundTasks,
                          customer_id: int = Depends(require_customer),
                          session: AsyncSession = Depends(get_session)) -> dict:
    p = await _own_project(project_id, customer_id, session)
    _require_draft(p)
    uploaded = await upload_file(file, background_tasks, f"/Customer Uploads/{customer_id}", session)
    session.add(ProjectItem(project_id=p.id, file_id=uploaded["id"], quantity=1))
    p.updated_at = _now()
    await session.commit()
    return await _project_dict(p, session)
