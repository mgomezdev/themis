from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Printer
from ...services.printer_client_factory import REGISTRY, get_printer_types_for_ui
from ...services.printer_manager import printer_manager
from ...services.profile_service import ProfileService

router = APIRouter(prefix="/api/v1/printers", tags=["printers"])


class PrinterCreate(BaseModel):
    name: str
    printer_type: str
    connection_config: dict
    orca_printer_profiles: list[str] = []
    current_orca_printer_profile: str | None = None


class PrinterUpdate(BaseModel):
    name: str | None = None
    connection_config: dict | None = None
    orca_printer_profiles: list[str] | None = None
    current_orca_printer_profile: str | None = None
    enabled: bool | None = None


class ActivePresetUpdate(BaseModel):
    preset: str


def _to_dict(p: Printer) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "printer_type": p.printer_type,
        "connection_config": p.connection_config,
        "awaiting_plate_clear": p.awaiting_plate_clear,
        "orca_printer_profiles": p.orca_printer_profiles,
        "current_orca_printer_profile": p.current_orca_printer_profile,
        "enabled": p.enabled,
    }


async def _get_or_404(printer_id: int, session: AsyncSession) -> Printer:
    printer = await session.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(404, f"Printer {printer_id} not found")
    return printer


@router.get("/types")
async def list_printer_types() -> list[dict]:
    return get_printer_types_for_ui()


@router.get("")
async def list_printers(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(select(Printer))
    return [_to_dict(p) for p in result.scalars().all()]


@router.post("", status_code=201)
async def create_printer(
    body: PrinterCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if body.printer_type not in REGISTRY:
        raise HTTPException(422, f"Unknown printer_type: {body.printer_type!r}. Valid types: {list(REGISTRY.keys())}")
    printer = Printer(
        name=body.name,
        printer_type=body.printer_type,
        connection_config=body.connection_config,
        orca_printer_profiles=body.orca_printer_profiles,
        current_orca_printer_profile=body.current_orca_printer_profile,
    )
    session.add(printer)
    await session.commit()
    await session.refresh(printer)
    return _to_dict(printer)


@router.get("/orca-presets")
async def list_orca_printer_presets() -> list[str]:
    svc = ProfileService()
    return svc.get_printer_preset_names()


@router.get("/{printer_id}/profiles")
async def get_profiles(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    printer = await _get_or_404(printer_id, session)
    if not printer.current_orca_printer_profile:
        return {"print_profiles": [], "filament_profiles": []}
    svc = ProfileService()
    return svc.get_compatible_profiles(printer.current_orca_printer_profile)


@router.get("/{printer_id}")
async def get_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return _to_dict(await _get_or_404(printer_id, session))


@router.patch("/{printer_id}")
async def update_printer(
    printer_id: int,
    body: PrinterUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    printer = await _get_or_404(printer_id, session)
    if body.name is not None:
        printer.name = body.name
    if body.connection_config is not None:
        printer.connection_config = body.connection_config
    if body.orca_printer_profiles is not None:
        printer.orca_printer_profiles = body.orca_printer_profiles
    if body.current_orca_printer_profile is not None:
        printer.current_orca_printer_profile = body.current_orca_printer_profile
    if body.enabled is not None:
        printer.enabled = body.enabled
    await session.commit()
    await session.refresh(printer)
    return _to_dict(printer)


@router.delete("/{printer_id}", status_code=204)
async def delete_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    printer = await _get_or_404(printer_id, session)
    await session.delete(printer)
    await session.commit()


@router.post("/{printer_id}/plate-cleared")
async def plate_cleared(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    printer = await _get_or_404(printer_id, session)
    printer.awaiting_plate_clear = False
    await session.commit()
    printer_manager.set_awaiting_plate_clear(printer_id, False)
    return {"ok": True}


@router.patch("/{printer_id}/active-preset")
async def switch_active_preset(
    printer_id: int,
    body: ActivePresetUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    printer = await _get_or_404(printer_id, session)
    if body.preset not in (printer.orca_printer_profiles or []):
        raise HTTPException(422, f"Preset {body.preset!r} not in this printer's configured profiles")
    printer.current_orca_printer_profile = body.preset
    await session.commit()
    await session.refresh(printer)
    return _to_dict(printer)
