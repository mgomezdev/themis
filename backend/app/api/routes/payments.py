from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import Project, ProjectPayment
from ...services.payments import adopt_manual_amount, payment_dict, sync_project_totals

router = APIRouter(prefix="/api/v1/projects/{project_id}/payments", tags=["payments"])

Method = Literal["cash", "card", "bank_transfer", "check", "other"]


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _check_not_future(d: date) -> date:
    # A day of slack so a local "today" ahead of UTC isn't rejected.
    if d > _today() + timedelta(days=1):
        raise HTTPException(422, "received_on can't be in the future")
    return d


class PaymentCreate(BaseModel):
    amount: float = Field(gt=0, le=1_000_000_000)
    received_on: Optional[date] = None  # default: today (UTC)
    method: Method = "other"
    note: Optional[str] = Field(default=None, max_length=500)


class PaymentPatch(BaseModel):
    amount: Optional[float] = Field(default=None, gt=0, le=1_000_000_000)
    received_on: Optional[date] = None
    method: Optional[Method] = None
    note: Optional[str] = Field(default=None, max_length=500)


async def _project_or_404(project_id: int, session: AsyncSession) -> Project:
    proj = await session.get(Project, project_id)
    if proj is None:
        raise HTTPException(404, "Project not found")
    return proj


async def _payment_or_404(project_id: int, payment_id: int, session: AsyncSession) -> ProjectPayment:
    pay = await session.get(ProjectPayment, payment_id)
    if pay is None or pay.project_id != project_id:
        raise HTTPException(404, f"Payment {payment_id} not found in project {project_id}")
    return pay


@router.get("", summary="List project payments (newest first)",
            responses={404: {"description": "Project not found"}},
            dependencies=[Depends(require_scope("projects:read"))])
async def list_payments(project_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    await _project_or_404(project_id, session)
    rows = (await session.execute(
        select(ProjectPayment).where(ProjectPayment.project_id == project_id)
        .order_by(ProjectPayment.received_on.desc(), ProjectPayment.id.desc())
    )).scalars().all()
    return [payment_dict(p) for p in rows]


@router.post("", status_code=201, summary="Record a payment",
             responses={404: {"description": "Project not found"}, 422: {"description": "Invalid payment"}},
             dependencies=[Depends(require_scope("projects:write"))])
async def add_payment(project_id: int, body: PaymentCreate, session: AsyncSession = Depends(get_session)) -> dict:
    """Adds the payment and re-derives the project's amount paid and payment status."""
    proj = await _project_or_404(project_id, session)
    await adopt_manual_amount(session, proj)
    pay = ProjectPayment(
        project_id=project_id, amount=round(body.amount, 2),
        received_on=_check_not_future(body.received_on or _today()).isoformat(),
        method=body.method, note=(body.note or "").strip() or None,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(pay)
    await session.flush()
    await sync_project_totals(session, proj)
    await session.commit()
    await session.refresh(pay)
    return payment_dict(pay)


@router.patch("/{payment_id}", summary="Edit a payment",
              responses={404: {"description": "Project or payment not found"}},
              dependencies=[Depends(require_scope("projects:write"))])
async def update_payment(project_id: int, payment_id: int, body: PaymentPatch,
                         session: AsyncSession = Depends(get_session)) -> dict:
    proj = await _project_or_404(project_id, session)
    pay = await _payment_or_404(project_id, payment_id, session)
    if body.amount is not None:
        pay.amount = round(body.amount, 2)
    if body.received_on is not None:
        pay.received_on = _check_not_future(body.received_on).isoformat()
    if body.method is not None:
        pay.method = body.method
    if "note" in body.model_fields_set:
        pay.note = (body.note or "").strip() or None
    await session.flush()
    await sync_project_totals(session, proj)
    await session.commit()
    await session.refresh(pay)
    return payment_dict(pay)


@router.delete("/{payment_id}", summary="Delete a payment",
               responses={404: {"description": "Project or payment not found"}},
               dependencies=[Depends(require_scope("projects:write"))])
async def delete_payment(project_id: int, payment_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Removes the payment and re-derives the project's totals. Deleting the last payment returns the
    project to unpaid / no amount (the legacy manual fields are then editable again)."""
    proj = await _project_or_404(project_id, session)
    pay = await _payment_or_404(project_id, payment_id, session)
    await session.delete(pay)
    await session.flush()
    remaining = (await session.execute(
        select(ProjectPayment.id).where(ProjectPayment.project_id == project_id).limit(1)
    )).first()
    if remaining is None:
        proj.amount_paid = None
        proj.payment_status = "unpaid"
        proj.updated_at = datetime.now(timezone.utc).isoformat()
    else:
        await sync_project_totals(session, proj)
    await session.commit()
    return {"deleted": payment_id}
