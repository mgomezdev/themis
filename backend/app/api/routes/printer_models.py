"""Printer-model registry API (BIZ-262): the stable-UUID catalog other capabilities query, and the user's enabled subset."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...services import printer_model_registry as registry, slicer_profiles
from ...services.providers.slicing import enabled_provider_ids

router = APIRouter(prefix="/api/v1/printer-models", tags=["printers"])


class ModelPatch(BaseModel):
    enabled: bool | None = None
    default_slicer_plugin_id: str | None = None


@router.get("", summary="List the printer-model registry", dependencies=[Depends(require_scope("printers:read"))])
async def list_printer_models(enabled: bool | None = None, plugin_id: str | None = None, usable: bool = False,
                              session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Every known model with its stable UUID. `enabled` filters the user's subset; `usable` also drops dormant models
    (plugin removed/disabled, or no longer declared). Dormant models stay listed otherwise: printers and files reference them."""
    await registry.sync_registry(session)
    await session.commit()
    return await registry.list_models(session, enabled=enabled, plugin_id=plugin_id, usable_only=usable)


@router.patch("/{model_uuid}", summary="Update model availability or its default slicer",
              responses={404: {"description": "Unknown model"}}, dependencies=[Depends(require_scope("printers:write"))])
async def patch_printer_model(model_uuid: str, body: ModelPatch, session: AsyncSession = Depends(get_session)) -> dict:
    row = await registry.get_model(session, model_uuid)
    if row is None:
        raise HTTPException(404, "Unknown printer model")
    if body.enabled is not None:
        row.enabled = body.enabled
    if "default_slicer_plugin_id" in body.model_fields_set:
        provider_id = body.default_slicer_plugin_id
        if provider_id is not None:
            if provider_id not in enabled_provider_ids():
                raise HTTPException(422, "Choose an enabled slicing provider")
            if not any(r.provider_plugin_id == provider_id and r.is_default
                       for r in await slicer_profiles.mapping_rows(session, model_uuid)):
                raise HTTPException(422, "Map a default machine profile for this model and slicer first")
        row.default_slicer_plugin_id = provider_id
    await session.commit()
    return registry.model_view(row, registry.dormant_reason(row))
