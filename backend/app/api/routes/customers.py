"""Staff management of customer accounts."""
from __future__ import annotations
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import ApiKey, Customer, Job, Project
from ...services.password import hash_password

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _to_dict(c: Customer) -> dict:
    return {"id": c.id, "name": c.name, "email": c.email, "enabled": c.enabled, "created_at": c.created_at,
            "phone": c.phone, "company": c.company, "notes": c.notes,
            # False until staff set a password — the account exists but can't sign in to the portal.
            "has_password": bool(c.password_hash)}


def _clean(v: str | None) -> str | None:
    return (v or "").strip() or None


class CustomerCreate(BaseModel):
    name: str
    email: str
    # Optional: without one the customer exists for bookkeeping but can't sign in to the
    # portal until staff set a password.
    password: str | None = None
    phone: str | None = None
    company: str | None = None
    notes: str | None = None


class CustomerPatch(BaseModel):
    name: str | None = None
    email: str | None = None
    password: str | None = None
    enabled: bool | None = None
    phone: str | None = None    # "" clears
    company: str | None = None  # "" clears
    notes: str | None = None    # "" clears


async def _email_taken(session: AsyncSession, email: str, exclude_id: int | None = None) -> bool:
    stmt = select(Customer.id).where(func.lower(Customer.email) == email.lower())
    if exclude_id is not None:
        stmt = stmt.where(Customer.id != exclude_id)
    return (await session.execute(stmt)).first() is not None


async def _revoke_sessions(session: AsyncSession, customer_id: int) -> None:
    await session.execute(
        update(ApiKey)
        .where(ApiKey.customer_id == customer_id, ApiKey.revoked_at.is_(None))
        .values(enabled=False, revoked_at=_now())
    )


def _parse_ts(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _project_status(jobs_total: int, jobs_complete: int) -> str:
    """Same buckets as the Projects screen's filter: no jobs yet / in progress / all done."""
    if jobs_total == 0:
        return "pending"
    return "completed" if jobs_complete == jobs_total else "active"


def _paid(p: Project) -> float:
    """Amount received. A project marked paid with no amount entered counts as paid in full."""
    if p.amount_paid is None and p.payment_status == "paid":
        return p.price or 0.0
    return p.amount_paid or 0.0


def _outstanding(p: Project) -> float:
    """Unpaid balance against the quoted price. A project marked paid owes nothing; one
    without a price has no known balance (counted separately as ``unpriced_unpaid``)."""
    if p.price is None or p.payment_status == "paid":
        return 0.0
    return max(p.price - _paid(p), 0.0)


async def _customer_projects(session: AsyncSession, customer_ids: list[int]) -> tuple[list[Project], dict[int, list[Job]]]:
    if not customer_ids:
        return [], {}
    projects = (await session.execute(
        select(Project).where(Project.customer_id.in_(customer_ids)).order_by(Project.created_at.desc())
    )).scalars().all()
    jobs_by_project: dict[int, list[Job]] = defaultdict(list)
    if projects:
        jobs = (await session.execute(
            select(Job).where(Job.project_id.in_([p.id for p in projects]))
        )).scalars().all()
        for j in jobs:
            jobs_by_project[j.project_id].append(j)
    return list(projects), jobs_by_project


def _project_summary(p: Project, jobs: list[Job]) -> dict:
    jobs_total = len(jobs)
    jobs_complete = sum(1 for j in jobs if j.status == "complete")
    costs = [j.filament_cost for j in jobs if j.filament_cost is not None]
    return {
        "id": p.id,
        "name": p.name,
        "stage": p.stage,
        "on_hold": p.on_hold,
        "due_date": p.due_date,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "jobs_total": jobs_total,
        "jobs_complete": jobs_complete,
        "status": _project_status(jobs_total, jobs_complete),
        "price": p.price,
        "amount_paid": p.amount_paid,
        "payment_status": p.payment_status,
        "filament_cost_total": round(sum(costs), 2) if costs else None,
        "outstanding": round(_outstanding(p), 2),
    }


FINANCIAL_WINDOWS = {"30d": 30, "60d": 60, "90d": 90, "all": None}


def _financials(projects: list[Project], jobs_by_project: dict[int, list[Job]], now: datetime) -> dict:
    """Revenue (amount paid), expenses (job filament cost), billed (quoted price) and
    outstanding balance, bucketed by project creation date. Payments carry no date of
    their own, so a project's money lands in the window the project was created in."""
    windows = {}
    for key, days in FINANCIAL_WINDOWS.items():
        cutoff = now - timedelta(days=days) if days is not None else None
        in_window = [p for p in projects
                     if cutoff is None or ((ts := _parse_ts(p.created_at)) is not None and ts >= cutoff)]
        revenue = sum(_paid(p) for p in in_window)
        expenses = sum(j.filament_cost for p in in_window for j in jobs_by_project.get(p.id, [])
                       if j.filament_cost is not None)
        windows[key] = {
            "project_count": len(in_window),
            "revenue": round(revenue, 2),
            "expenses": round(expenses, 2),
            "profit": round(revenue - expenses, 2),
            "billed": round(sum(p.price or 0.0 for p in in_window), 2),
            "outstanding": round(sum(_outstanding(p) for p in in_window), 2),
        }
    return {
        "windows": windows,
        "unpriced_unpaid": sum(1 for p in projects if p.price is None and p.payment_status != "paid"),
    }


@router.get("", summary="List customers", dependencies=[Depends(require_scope("customers:read"))])
async def list_customers(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Customers with per-customer project counts and outstanding balance."""
    rows = (await session.execute(select(Customer).order_by(Customer.name))).scalars().all()
    projects, jobs_by_project = await _customer_projects(session, [c.id for c in rows])
    by_customer: dict[int, list[dict]] = defaultdict(list)
    for p in projects:
        by_customer[p.customer_id].append(_project_summary(p, jobs_by_project.get(p.id, [])))
    out = []
    for c in rows:
        summaries = by_customer.get(c.id, [])
        out.append({
            **_to_dict(c),
            "project_count": len(summaries),
            "active_project_count": sum(1 for s in summaries if s["status"] != "completed"),
            "outstanding": round(sum(s["outstanding"] for s in summaries), 2),
            "last_project_at": summaries[0]["created_at"] if summaries else None,
        })
    return out


def _norm(s: str | None) -> str:
    return " ".join((s or "").split()).lower()


@router.get("/unlinked-projects", summary="Projects with a typed customer name but no customer account",
            dependencies=[Depends(require_scope("customers:read"))])
async def unlinked_projects(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Projects whose free-text ``customer`` is set but ``customer_id`` isn't, each with a
    suggested account when the text exactly matches (case/space-insensitive) one customer's
    name, company, or email. Staff confirm before anything is linked."""
    customers = (await session.execute(select(Customer))).scalars().all()
    index: dict[str, set[int]] = defaultdict(set)
    for c in customers:
        for key in (c.name, c.company, c.email):
            if _norm(key):
                index[_norm(key)].add(c.id)
    projects = (await session.execute(
        select(Project).where(Project.customer_id.is_(None), Project.customer != "")
        .order_by(Project.created_at.desc())
    )).scalars().all()
    out = []
    for p in projects:
        if not _norm(p.customer):
            continue
        matches = index.get(_norm(p.customer), set())
        out.append({
            "project_id": p.id, "project_name": p.name, "customer_text": p.customer,
            "created_at": p.created_at,
            "suggested_customer_id": next(iter(matches)) if len(matches) == 1 else None,
        })
    return out


class ProjectLink(BaseModel):
    project_id: int
    customer_id: int


class LinkProjectsBody(BaseModel):
    links: list[ProjectLink]


@router.post("/link-projects", summary="Link projects to customer accounts",
             dependencies=[Depends(require_scope("customers:write")), Depends(require_scope("projects:write"))])
async def link_projects(body: LinkProjectsBody, session: AsyncSession = Depends(get_session)) -> dict:
    customer_ids = {c for (c,) in (await session.execute(
        select(Customer.id).where(Customer.id.in_({l.customer_id for l in body.links}))
    )).all()}
    missing = {l.customer_id for l in body.links} - customer_ids
    if missing:
        raise HTTPException(404, f"Customer(s) not found: {sorted(missing)}")
    linked = 0
    for link in body.links:
        p = await session.get(Project, link.project_id)
        if p is None:
            raise HTTPException(404, f"Project {link.project_id} not found")
        p.customer_id = link.customer_id
        p.order_type = "customer"
        linked += 1
    await session.commit()
    return {"linked": linked}


@router.get("/{customer_id}", summary="Get customer with projects and financial summary",
            dependencies=[Depends(require_scope("customers:read"))])
async def get_customer(customer_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    c = await session.get(Customer, customer_id)
    if c is None:
        raise HTTPException(404, "Customer not found")
    projects, jobs_by_project = await _customer_projects(session, [c.id])
    return {
        **_to_dict(c),
        "projects": [_project_summary(p, jobs_by_project.get(p.id, [])) for p in projects],
        "financials": _financials(projects, jobs_by_project, datetime.now(timezone.utc)),
    }


@router.post("", status_code=201, summary="Create customer",
             dependencies=[Depends(require_scope("customers:write"))])
async def create_customer(body: CustomerCreate, session: AsyncSession = Depends(get_session)) -> dict:
    email = body.email.strip().lower()
    if not email:
        raise HTTPException(422, "email is required")
    if "@" not in email:  # also keeps the admin username ("admin") from being a customer email
        raise HTTPException(422, "email must be an email address")
    if await _email_taken(session, email):
        raise HTTPException(409, "Email already in use")
    # No password → empty hash, which login always rejects (see session.py).
    password_hash = await asyncio.to_thread(hash_password, body.password) if body.password else ""
    c = Customer(name=body.name.strip() or email, email=email,
                 password_hash=password_hash, enabled=True, created_at=_now(),
                 phone=_clean(body.phone), company=_clean(body.company), notes=_clean(body.notes))
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return _to_dict(c)


@router.patch("/{customer_id}", summary="Update customer",
              dependencies=[Depends(require_scope("customers:write"))])
async def update_customer(customer_id: int, body: CustomerPatch,
                          session: AsyncSession = Depends(get_session)) -> dict:
    """Disabling a customer or changing their password signs them out everywhere."""
    c = await session.get(Customer, customer_id)
    if c is None:
        raise HTTPException(404, "Customer not found")
    if body.name is not None:
        c.name = body.name.strip() or c.name
    if body.email is not None:
        email = body.email.strip().lower()
        if not email:
            raise HTTPException(422, "email must not be empty")
        if "@" not in email:
            raise HTTPException(422, "email must be an email address")
        if await _email_taken(session, email, exclude_id=c.id):
            raise HTTPException(409, "Email already in use")
        c.email = email
    if body.password:
        c.password_hash = await asyncio.to_thread(hash_password, body.password)
        await _revoke_sessions(session, c.id)
    if body.enabled is not None:
        c.enabled = body.enabled
        if not body.enabled:
            await _revoke_sessions(session, c.id)
    for field in ("phone", "company", "notes"):
        if field in body.model_fields_set:
            setattr(c, field, _clean(getattr(body, field)))
    await session.commit()
    await session.refresh(c)
    return _to_dict(c)


@router.delete("/{customer_id}", summary="Delete customer",
               dependencies=[Depends(require_scope("customers:write"))])
async def delete_customer(customer_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Removes the account and signs it out. Its projects are kept, unlinked, with the
    customer's name left as their typed customer label so history still reads correctly."""
    c = await session.get(Customer, customer_id)
    if c is None:
        raise HTTPException(404, "Customer not found")
    projects = (await session.execute(select(Project).where(Project.customer_id == c.id))).scalars().all()
    for p in projects:
        p.customer_id = None
        if not (p.customer or "").strip():
            p.customer = c.name
    await session.execute(delete(ApiKey).where(ApiKey.customer_id == c.id))
    await session.delete(c)
    await session.commit()
    return {"deleted": customer_id, "projects_unlinked": len(projects)}
