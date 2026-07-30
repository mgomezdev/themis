from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import MaintenanceItem, MaintenanceTrigger, Printer, PrinterMaintenanceState
from ...services import maintenance_service
from ...services.maintenance_service import COMMON_MAINTENANCE_TEMPLATES
from .printers import orca_machine_catalog

router = APIRouter(prefix="/api/v1/maintenance", tags=["maintenance"])

VALID_TRIGGER_TYPES = ("calendar", "job_time", "job_count")


class TriggerIn(BaseModel):
    trigger_type: str
    amount: float
    unit: str | None = None


class MaintenanceItemCreate(BaseModel):
    name: str
    scope: str = "general"
    machine_vendor: str | None = None
    machine_model: str | None = None
    notes: str | None = None
    triggers: list[TriggerIn] = []


class TriggersReplace(BaseModel):
    triggers: list[TriggerIn]


class MaintenanceItemPatch(BaseModel):
    name: str | None = None
    scope: str | None = None
    machine_vendor: str | None = None
    machine_model: str | None = None
    enabled: bool | None = None
    notes: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _triggers_for(session: AsyncSession, item_id: int) -> list[MaintenanceTrigger]:
    return list((await session.execute(
        select(MaintenanceTrigger).where(MaintenanceTrigger.maintenance_item_id == item_id)
    )).scalars().all())


def _validate_trigger_types(triggers: list[TriggerIn]) -> None:
    for t in triggers:
        if t.trigger_type not in VALID_TRIGGER_TYPES:
            raise HTTPException(
                422, f"Invalid trigger_type: {t.trigger_type!r}. Valid: calendar, job_time, job_count"
            )


def _trigger_dict(t: MaintenanceTrigger) -> dict:
    return {"id": t.id, "trigger_type": t.trigger_type, "amount": t.amount, "unit": t.unit}


async def _item_dict(session: AsyncSession, item: MaintenanceItem) -> dict:
    triggers = await _triggers_for(session, item.id)
    return {
        "id": item.id, "name": item.name, "scope": item.scope,
        "machine_vendor": item.machine_vendor, "machine_model": item.machine_model,
        "enabled": item.enabled, "notes": item.notes,
        "triggers": [_trigger_dict(t) for t in triggers],
    }


@router.get("/items", summary="List maintenance items")
async def list_items(session: AsyncSession = Depends(get_session)) -> list[dict]:
    items = (await session.execute(select(MaintenanceItem).order_by(MaintenanceItem.name))).scalars().all()
    return [await _item_dict(session, i) for i in items]


@router.post(
    "/items", status_code=201, summary="Create maintenance item",
    responses={422: {"description": "scope='model' requires machine_vendor and machine_model"}},
)
async def create_item(body: MaintenanceItemCreate, session: AsyncSession = Depends(get_session)) -> dict:
    if body.scope not in ("general", "model"):
        raise HTTPException(422, "scope must be 'general' or 'model'")
    if body.scope == "model" and not (body.machine_vendor and body.machine_model):
        raise HTTPException(422, "machine_vendor and machine_model are required when scope='model'")
    _validate_trigger_types(body.triggers)
    now = _now()
    item = MaintenanceItem(
        name=body.name, scope=body.scope,
        machine_vendor=body.machine_vendor if body.scope == "model" else None,
        machine_model=body.machine_model if body.scope == "model" else None,
        notes=body.notes, created_at=now, updated_at=now,
    )
    session.add(item)
    await session.flush()
    for t in body.triggers:
        session.add(MaintenanceTrigger(
            maintenance_item_id=item.id, trigger_type=t.trigger_type, amount=t.amount, unit=t.unit,
        ))
    await session.commit()
    await session.refresh(item)
    return await _item_dict(session, item)


@router.patch(
    "/items/{item_id}", summary="Update maintenance item",
    responses={
        404: {"description": "Maintenance item not found"},
        422: {"description": "scope='model' requires machine_vendor and machine_model"},
    },
)
async def update_item(item_id: int, body: MaintenanceItemPatch, session: AsyncSession = Depends(get_session)) -> dict:
    item = await session.get(MaintenanceItem, item_id)
    if item is None:
        raise HTTPException(404, f"Maintenance item {item_id} not found")
    if body.scope is not None and body.scope not in ("general", "model"):
        raise HTTPException(422, "scope must be 'general' or 'model'")

    # Validate the *effective* post-patch scope/vendor/model invariant, not just
    # what's newly submitted — scope may be unchanged while vendor/model are
    # missing from this particular PATCH body (or vice versa).
    effective_scope = body.scope if body.scope is not None else item.scope
    effective_vendor = body.machine_vendor if body.machine_vendor is not None else item.machine_vendor
    effective_model = body.machine_model if body.machine_model is not None else item.machine_model
    if effective_scope == "model" and not (effective_vendor and effective_model):
        raise HTTPException(422, "machine_vendor and machine_model are required when scope='model'")

    if body.name is not None:
        item.name = body.name
    if body.scope is not None:
        item.scope = body.scope
    if effective_scope == "model":
        if body.machine_vendor is not None:
            item.machine_vendor = body.machine_vendor
        if body.machine_model is not None:
            item.machine_model = body.machine_model
    else:
        # Mirror create_item: a general-scoped item never carries vendor/model.
        item.machine_vendor = None
        item.machine_model = None
    if body.enabled is not None:
        item.enabled = body.enabled
    if body.notes is not None:
        item.notes = body.notes
    item.updated_at = _now()
    await session.commit()
    return await _item_dict(session, item)


@router.delete(
    "/items/{item_id}", summary="Delete maintenance item",
    responses={404: {"description": "Maintenance item not found"}},
)
async def delete_item(item_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    item = await session.get(MaintenanceItem, item_id)
    if item is None:
        raise HTTPException(404, f"Maintenance item {item_id} not found")
    # Explicit cascade (SQLite FK cascade is not enforced by default here).
    for t in await _triggers_for(session, item_id):
        await session.delete(t)
    for s in (await session.execute(
        select(PrinterMaintenanceState).where(PrinterMaintenanceState.maintenance_item_id == item_id)
    )).scalars().all():
        await session.delete(s)
    await session.delete(item)
    await session.commit()
    return {"deleted": item_id}


@router.put(
    "/items/{item_id}/triggers", summary="Replace an item's triggers",
    responses={404: {"description": "Maintenance item not found"}},
)
async def replace_triggers(item_id: int, body: TriggersReplace, session: AsyncSession = Depends(get_session)) -> dict:
    item = await session.get(MaintenanceItem, item_id)
    if item is None:
        raise HTTPException(404, f"Maintenance item {item_id} not found")
    _validate_trigger_types(body.triggers)
    for existing in await _triggers_for(session, item_id):
        await session.delete(existing)
    await session.flush()
    for t in body.triggers:
        session.add(MaintenanceTrigger(
            maintenance_item_id=item_id, trigger_type=t.trigger_type, amount=t.amount, unit=t.unit,
        ))
    item.updated_at = _now()
    await session.commit()
    return await _item_dict(session, item)


@router.get("/templates", summary="Suggested common maintenance items")
async def list_templates() -> list[dict]:
    return COMMON_MAINTENANCE_TEMPLATES


# N+1 by design: one query per (printer, applicable item) pair inside
# compute_due_status. Fine at the expected scale (<20 printers, <30 items);
# revisit with a batch fetch if that scale assumption changes.
@router.get("/status", summary="Due status for every printer x applicable maintenance item")
async def maintenance_status(session: AsyncSession = Depends(get_session)) -> list[dict]:
    printers = list((await session.execute(select(Printer))).scalars().all())
    items = list((await session.execute(
        select(MaintenanceItem).where(MaintenanceItem.enabled.is_(True))
    )).scalars().all())
    triggers_by_item: dict[int, list[MaintenanceTrigger]] = {}
    for item in items:
        triggers_by_item[item.id] = await _triggers_for(session, item.id)
    catalog = await orca_machine_catalog()
    return await maintenance_service.compute_due_status(session, printers, items, triggers_by_item, catalog)


@router.post(
    "/printers/{printer_id}/items/{item_id}/complete",
    summary="Mark a maintenance item done for a printer",
    responses={404: {"description": "Printer or maintenance item not found"}},
)
async def complete_item(printer_id: int, item_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    printer = await session.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(404, f"Printer {printer_id} not found")
    item = await session.get(MaintenanceItem, item_id)
    if item is None:
        raise HTTPException(404, f"Maintenance item {item_id} not found")
    state = await maintenance_service.mark_done(session, printer, item)
    return {"printer_id": printer_id, "item_id": item_id, "last_done_at": state.last_done_at}
