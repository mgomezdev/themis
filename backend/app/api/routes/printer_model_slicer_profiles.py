"""Model -> slicer machine profile mapping API (BIZ-294).

`/api/v1/printer-models/{model_uuid}/slicer-profiles`: the machine profile a printer MODEL uses, per slicing provider and nozzle variant,
set once instead of per printer. Exactly one default variant per (model, provider). `/suggestions` asks the provider (when it can, see
`SlicingProvider.suggest_machines`) which profile and nozzle variants probably fit; entering a mapping by hand never needs it."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import Printer, PrinterModelRecord, PrinterModelSlicerProfile
from ...plugins import providers_of
from ...services import slicer_profiles
from ...services.providers.slicing import SlicingProviderError, default_provider_id, slicing_provider_for

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/printer-models/{model_uuid}/slicer-profiles", tags=["printers"])


class MappingCreate(BaseModel):
    provider_plugin_id: str
    nozzle_diameter_mm: float = Field(gt=0, le=2.0)
    flow: str = "standard"
    machine_profile: str
    is_default: bool = False

    @field_validator("flow")
    @classmethod
    def _flow(cls, v: str) -> str:
        if v not in slicer_profiles.FLOWS:
            raise ValueError("flow must be standard or high_flow")
        return v

    @field_validator("machine_profile")
    @classmethod
    def _profile(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("machine_profile must not be blank")
        return v.strip()


class MappingPatch(BaseModel):
    machine_profile: str | None = None
    is_default: bool | None = None

    @field_validator("machine_profile")
    @classmethod
    def _profile(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("machine_profile must not be blank")
        return v.strip() if v else v


def _view(r: PrinterModelSlicerProfile) -> dict:
    return {"id": r.id, "model_uuid": r.model_uuid, "provider_plugin_id": r.provider_plugin_id,
            "nozzle_diameter_mm": r.nozzle_diameter_mm, "flow": r.flow, "machine_profile": r.machine_profile,
            "is_default": bool(r.is_default)}


async def _model_or_404(session: AsyncSession, model_uuid: str) -> PrinterModelRecord:
    rec = await session.get(PrinterModelRecord, model_uuid)
    if rec is None:
        raise HTTPException(404, "Unknown printer model")
    return rec


async def _row_or_404(session: AsyncSession, model_uuid: str, mapping_id: int) -> PrinterModelSlicerProfile:
    row = await session.get(PrinterModelSlicerProfile, mapping_id)
    if row is None or row.model_uuid != model_uuid:
        raise HTTPException(404, "Unknown slicer profile mapping")
    return row


async def _make_default(session: AsyncSession, row: PrinterModelSlicerProfile) -> None:
    """Exactly one default per (model, provider): demote the others first so the partial unique index never sees two."""
    await session.execute(update(PrinterModelSlicerProfile).where(
        PrinterModelSlicerProfile.model_uuid == row.model_uuid,
        PrinterModelSlicerProfile.provider_plugin_id == row.provider_plugin_id,
        PrinterModelSlicerProfile.id != row.id).values(is_default=False))
    row.is_default = True
    await session.flush()


@router.get("", summary="List a model's slicer profile mappings", responses={404: {"description": "Unknown model"}},
            dependencies=[Depends(require_scope("printers:read"))])
async def list_mappings(model_uuid: str, session: AsyncSession = Depends(get_session)) -> list[dict]:
    await _model_or_404(session, model_uuid)
    return [_view(r) for r in await slicer_profiles.mapping_rows(session, model_uuid)]


@router.post("", status_code=201, summary="Map a nozzle variant of a model to a slicer machine profile",
             responses={404: {"description": "Unknown model"}, 409: {"description": "That variant is already mapped"},
                        422: {"description": "Not a slicing provider, or a bad variant/profile"}},
             dependencies=[Depends(require_scope("printers:write"))])
async def create_mapping(model_uuid: str, body: MappingCreate, session: AsyncSession = Depends(get_session)) -> dict:
    """The first variant mapped for a (model, provider) becomes its default; `is_default` on a later one moves the default to it."""
    await _model_or_404(session, model_uuid)
    if body.provider_plugin_id not in {m.id for m in providers_of("gcode.produce")}:
        raise HTTPException(422, f"{body.provider_plugin_id!r} is not a plugin that provides gcode.produce")
    mine = [r for r in await slicer_profiles.mapping_rows(session, model_uuid) if r.provider_plugin_id == body.provider_plugin_id]
    variant = slicer_profiles.NozzleVariant(body.nozzle_diameter_mm, body.flow)
    if any(slicer_profiles.variant_of(r) == variant for r in mine):
        raise HTTPException(409, f"A profile is already mapped for {variant.label}; edit it instead")
    row = PrinterModelSlicerProfile(model_uuid=model_uuid, provider_plugin_id=body.provider_plugin_id,
                                    nozzle_diameter_mm=variant.diameter_mm, flow=variant.flow,
                                    machine_profile=body.machine_profile, is_default=False)
    session.add(row)
    await session.flush()
    if body.is_default or not mine:
        await _make_default(session, row)
    await session.commit()
    return _view(row)


@router.patch("/{mapping_id}", summary="Change a mapping's machine profile or make it the default",
              responses={404: {"description": "Unknown mapping"}, 422: {"description": "A default cannot be unset directly"}},
              dependencies=[Depends(require_scope("printers:write"))])
async def patch_mapping(model_uuid: str, mapping_id: int, body: MappingPatch,
                        session: AsyncSession = Depends(get_session)) -> dict:
    row = await _row_or_404(session, model_uuid, mapping_id)
    if body.is_default is False and row.is_default:
        raise HTTPException(422, "A model always has a default variant: make another variant the default instead")
    if body.machine_profile is not None:
        row.machine_profile = body.machine_profile
    if body.is_default:
        await _make_default(session, row)
    await session.commit()
    return _view(row)


@router.delete("/{mapping_id}", status_code=204, summary="Remove a mapping",
               responses={404: {"description": "Unknown mapping"},
                          409: {"description": "It is the default of a model with other variants, or a printer uses it"}},
               dependencies=[Depends(require_scope("printers:write"))])
async def delete_mapping(model_uuid: str, mapping_id: int, session: AsyncSession = Depends(get_session)) -> None:
    row = await _row_or_404(session, model_uuid, mapping_id)
    siblings = [r for r in await slicer_profiles.mapping_rows(session, model_uuid)
                if r.provider_plugin_id == row.provider_plugin_id and r.id != row.id]
    if row.is_default and siblings:
        raise HTTPException(409, "This is the default variant: make another variant the default before removing it")
    variant = slicer_profiles.variant_of(row)
    printers = (await session.execute(select(Printer).where(Printer.model_uuid == model_uuid))).scalars().all()
    users = [p.name for p in printers if slicer_profiles.NozzleVariant.parse(p.nozzle_variant) == variant]
    if users:
        raise HTTPException(409, f"{', '.join(users)} use the {variant.label} nozzle: change them first")
    if row.is_default and not siblings:
        followers = [p.name for p in printers if not p.nozzle_variant and not p.machine_profile_override]
        if followers:
            raise HTTPException(409, f"{', '.join(followers)} follow this model's default profile: set a profile override or remove "
                                     "those printers before removing the last profile")
    await session.delete(row)
    await session.commit()


@router.get("/suggestions", summary="Ask a slicing provider which profile and nozzle variants fit this model",
            responses={404: {"description": "Unknown model"}}, dependencies=[Depends(require_scope("printers:read"))])
async def suggestions(model_uuid: str, provider_plugin_id: str | None = None,
                      session: AsyncSession = Depends(get_session)) -> dict:
    """Never fails for an unavailable provider: `available` is false (manual entry still works) and `error` says why."""
    rec = await _model_or_404(session, model_uuid)
    pid = provider_plugin_id or default_provider_id()
    out: dict = {"provider_plugin_id": pid, "available": False, "candidates": [], "variants": [], "error": None}
    provider = slicing_provider_for(pid) if pid else None
    if provider is None:
        out["error"] = "No slicing provider is configured"
        return out
    if not provider.SUGGEST_PROFILES:
        out["error"] = "This slicing provider cannot suggest profiles"
        return out
    manufacturer, model, key = rec.manufacturer_name, rec.display_name, f"{rec.manufacturer_id}/{rec.model_id}"
    try:
        candidates = await asyncio.to_thread(provider.suggest_machines, manufacturer, model, key)
        variants = await asyncio.to_thread(provider.nozzle_variants, manufacturer, model, key)
    except SlicingProviderError as e:
        out["error"] = str(e)
        return out
    out["available"] = True
    out["candidates"] = [{"name": c.name, "nozzle_diameter_mm": c.nozzle_diameter_mm, "flow": c.flow, "ref": c.ref,
                          "confidence": c.confidence, "match_reason": c.match_reason} for c in candidates]
    out["variants"] = [{"name": v.name, "nozzle_diameter_mm": v.nozzle_diameter_mm, "flow": v.flow, "ref": v.ref,
                        "is_default_guess": v.is_default_guess} for v in variants]
    return out
