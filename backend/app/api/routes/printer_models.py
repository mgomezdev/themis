"""Printer-model registry API (BIZ-262): the stable-UUID catalog other capabilities query, and the user's enabled subset."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...services import printer_model_registry as registry

router = APIRouter(prefix="/api/v1/printer-models", tags=["printers"])


class ModelPatch(BaseModel):
    enabled: bool


@router.get("", summary="List the printer-model registry", dependencies=[Depends(require_scope("printers:read"))])
async def list_printer_models(enabled: bool | None = None, plugin_id: str | None = None, usable: bool = False,
                              session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Every known model with its stable UUID. `enabled` filters the user's subset; `usable` also drops dormant models
    (plugin removed/disabled, or no longer declared). Dormant models stay listed otherwise: printers and files reference them."""
    await registry.sync_registry(session)
    await session.commit()
    return await registry.list_models(session, enabled=enabled, plugin_id=plugin_id, usable_only=usable)


@router.patch("/{model_uuid}", summary="Enable or disable a model for setup and eligibility pickers",
              responses={404: {"description": "Unknown model"}}, dependencies=[Depends(require_scope("printers:write"))])
async def patch_printer_model(model_uuid: str, body: ModelPatch, session: AsyncSession = Depends(get_session)) -> dict:
    row = await registry.get_model(session, model_uuid)
    if row is None:
        raise HTTPException(404, "Unknown printer model")
    row.enabled = body.enabled
    await session.commit()
    return registry.model_view(row, registry.dormant_reason(row))
