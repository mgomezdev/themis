"""Project payments: the single source of truth for what a customer has paid.

Once a project has at least one payment row, `Project.amount_paid` and `Project.payment_status` are *derived*
here (never typed in). Projects with no rows keep the legacy manual fields, so API clients that only know
`amount_paid`/`payment_status` (e.g. Ordinus) and "marked paid, no amount" projects keep working."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Project, ProjectPayment


def paid_amount(p: Project) -> float:
    """Amount received. A project marked paid with no amount entered counts as paid in full."""
    if p.amount_paid is None and p.payment_status == "paid":
        return p.price or 0.0
    return p.amount_paid or 0.0


def outstanding(p: Project) -> float:
    """Unpaid balance against the quoted price. A project marked paid owes nothing; one without a price has no
    known balance (reported separately as unpriced)."""
    if p.price is None or p.payment_status == "paid":
        return 0.0
    return max(p.price - paid_amount(p), 0.0)


def derive_status(price: float | None, total: float) -> str:
    """unpaid → nothing received; paid → received covers the quoted price; otherwise partial.
    With no quoted price there's nothing to compare against, so received money reads as partial."""
    if total <= 0:
        return "unpaid"
    if price is not None and round(total, 2) >= round(price, 2):
        return "paid"
    return "partial"


async def payment_total(session: AsyncSession, project_id: int) -> tuple[float, int]:
    total, count = (await session.execute(
        select(func.coalesce(func.sum(ProjectPayment.amount), 0.0), func.count(ProjectPayment.id))
        .where(ProjectPayment.project_id == project_id)
    )).one()
    return round(float(total), 2), int(count)


async def has_payments(session: AsyncSession, project_id: int) -> bool:
    return (await session.execute(
        select(ProjectPayment.id).where(ProjectPayment.project_id == project_id).limit(1)
    )).first() is not None


OPENING_NOTE = "Opening balance (from amount paid)"


async def adopt_manual_amount(session: AsyncSession, project: Project) -> None:
    """A project with a hand-entered `amount_paid` but no payment rows gets that amount recorded as one opening
    payment before the first real payment is added — otherwise deriving the total from rows would silently
    drop what was already entered. Dated the project's creation day (as reporting already bucketed it)."""
    if await has_payments(session, project.id):
        return
    amount = project.amount_paid
    if not amount and project.payment_status == "paid" and project.price:
        amount = project.price   # legacy "marked paid, no amount" counts as paid in full — keep it that way
    if not amount or amount <= 0:
        return
    session.add(ProjectPayment(
        project_id=project.id, amount=round(amount, 2), received_on=(project.created_at or "")[:10]
        or datetime.now(timezone.utc).date().isoformat(),
        method="other", note=OPENING_NOTE, created_at=datetime.now(timezone.utc).isoformat()))
    await session.flush()


async def sync_project_totals(session: AsyncSession, project: Project) -> None:
    """Recompute amount_paid/payment_status from the project's payments (flush, caller commits).
    No-op when the project has none, so legacy manual values are left alone."""
    total, count = await payment_total(session, project.id)
    if count == 0:
        return
    project.amount_paid = total
    project.payment_status = derive_status(project.price, total)
    project.updated_at = datetime.now(timezone.utc).isoformat()


def payment_dict(p: ProjectPayment, project_name: str | None = None) -> dict:
    out = {
        "id": p.id, "project_id": p.project_id, "amount": p.amount, "received_on": p.received_on,
        "method": p.method, "note": p.note, "created_at": p.created_at,
    }
    if project_name is not None:
        out["project_name"] = project_name
    return out
