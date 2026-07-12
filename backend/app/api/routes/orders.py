from __future__ import annotations
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Job, Order

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


class OrderPartIn(BaseModel):
    id: str | None = None
    name: str
    qty: int = 1
    material: str = ""
    est_minutes: int = 0
    filament_id: int | None = None
    filament_color: str | None = None


class OrderCreate(BaseModel):
    order_type: str
    customer: str
    title: str
    due_date: str | None = None
    notes: str | None = None
    parts: list[OrderPartIn] = []


class OrderPatch(BaseModel):
    order_type: str | None = None
    customer: str | None = None
    title: str | None = None
    due_date: str | None = None
    notes: str | None = None
    on_hold: bool | None = None
    parts: list[OrderPartIn] | None = None


def _normalize_parts(parts: list[OrderPartIn]) -> list[dict]:
    out = []
    for p in parts:
        out.append({
            "id": p.id or uuid.uuid4().hex,
            "name": p.name,
            "qty": p.qty,
            "material": p.material,
            "est_minutes": p.est_minutes,
            "filament_id": p.filament_id,
            "filament_color": p.filament_color,
        })
    return out


async def _derive(session: AsyncSession, order: Order) -> tuple[str, float, int]:
    result = await session.execute(select(Job.status).where(Job.order_id == order.id))
    statuses = [r[0] for r in result.all()]
    active = [s for s in statuses if s != "cancelled"]
    completed = [s for s in active if s == "complete"]
    # Manual hold overrides derived state (per spec).
    if order.on_hold:
        status = "hold"
    elif not active:
        status = "queued"
    elif all(s == "complete" for s in active):
        status = "complete"
    else:
        status = "in_progress"
    progress = (len(completed) / len(active)) if active else 0.0
    return status, round(progress, 4), len(active)


async def _to_dict(session: AsyncSession, o: Order, with_jobs: bool = False) -> dict:
    status, progress, job_count = await _derive(session, o)
    data = {
        "id": o.id,
        "order_type": o.order_type,
        "customer": o.customer,
        "title": o.title,
        "due_date": o.due_date,
        "notes": o.notes,
        "on_hold": o.on_hold,
        "parts": o.parts or [],
        "status": status,
        "progress": progress,
        "job_count": job_count,
        "created_at": o.created_at,
        "updated_at": o.updated_at,
    }
    if with_jobs:
        result = await session.execute(
            select(Job).where(Job.order_id == o.id, Job.status != "cancelled")
            .order_by(Job.queue_position)
        )
        data["jobs"] = [
            {
                "id": j.id,
                "status": j.status,
                "plate_number": j.plate_number,
                "uploaded_file_id": j.uploaded_file_id,
                "queue_position": j.queue_position,
            }
            for j in result.scalars().all()
        ]
    return data


async def _get_or_404(order_id: int, session: AsyncSession) -> Order:
    o = await session.get(Order, order_id)
    if o is None:
        raise HTTPException(404, f"Order {order_id} not found")
    return o


@router.get("", summary="List orders")
async def list_orders(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """All orders ordered by creation date descending. Each order includes derived
    status (queued / in_progress / complete / hold) and progress fraction."""
    result = await session.execute(select(Order).order_by(Order.created_at.desc()))
    return [await _to_dict(session, o) for o in result.scalars().all()]


@router.post("", status_code=201, summary="Create order")
async def create_order(body: OrderCreate, session: AsyncSession = Depends(get_session)) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    order = Order(
        order_type=body.order_type,
        customer=body.customer,
        title=body.title,
        due_date=body.due_date,
        notes=body.notes,
        on_hold=False,
        parts=_normalize_parts(body.parts),
        created_at=now,
        updated_at=now,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return await _to_dict(session, order, with_jobs=True)


@router.get(
    "/{order_id}",
    summary="Get order",
    responses={
        404: {"description": "Order not found"},
    },
)
async def get_order(order_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Order detail including all non-cancelled jobs."""
    return await _to_dict(session, await _get_or_404(order_id, session), with_jobs=True)


@router.patch(
    "/{order_id}",
    summary="Update order",
    responses={
        404: {"description": "Order not found"},
    },
)
async def patch_order(order_id: int, body: OrderPatch,
                      session: AsyncSession = Depends(get_session)) -> dict:
    order = await _get_or_404(order_id, session)
    fields = body.model_dump(exclude_unset=True)
    if "parts" in fields and fields["parts"] is not None:
        order.parts = _normalize_parts([OrderPartIn(**p) for p in fields.pop("parts")])
    else:
        fields.pop("parts", None)
    for k, v in fields.items():
        setattr(order, k, v)
    order.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()
    await session.refresh(order)
    return await _to_dict(session, order, with_jobs=True)


@router.delete(
    "/{order_id}",
    status_code=204,
    summary="Delete order",
    responses={
        404: {"description": "Order not found"},
    },
)
async def delete_order(order_id: int, session: AsyncSession = Depends(get_session)) -> None:
    order = await _get_or_404(order_id, session)
    await session.execute(update(Job).where(Job.order_id == order_id).values(order_id=None))
    await session.delete(order)
    await session.commit()
