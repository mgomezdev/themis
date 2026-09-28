"""Staff management of customer accounts."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import ApiKey, Customer
from ...services.password import hash_password

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _to_dict(c: Customer) -> dict:
    return {"id": c.id, "name": c.name, "email": c.email, "enabled": c.enabled, "created_at": c.created_at}


class CustomerCreate(BaseModel):
    name: str
    email: str
    password: str


class CustomerPatch(BaseModel):
    name: str | None = None
    email: str | None = None
    password: str | None = None
    enabled: bool | None = None


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


@router.get("", summary="List customers", dependencies=[Depends(require_scope("customers:read"))])
async def list_customers(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await session.execute(select(Customer).order_by(Customer.name))).scalars().all()
    return [_to_dict(c) for c in rows]


@router.post("", status_code=201, summary="Create customer",
             dependencies=[Depends(require_scope("customers:write"))])
async def create_customer(body: CustomerCreate, session: AsyncSession = Depends(get_session)) -> dict:
    email = body.email.strip().lower()
    if not email or not body.password:
        raise HTTPException(422, "email and password are required")
    if await _email_taken(session, email):
        raise HTTPException(409, "Email already in use")
    c = Customer(name=body.name.strip() or email, email=email,
                 password_hash=await asyncio.to_thread(hash_password, body.password), enabled=True, created_at=_now())
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
    await session.commit()
    await session.refresh(c)
    return _to_dict(c)
