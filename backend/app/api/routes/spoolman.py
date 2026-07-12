from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import SpoolmanConfig
from ...services import spoolman_service

router = APIRouter(prefix="/api/v1/spoolman", tags=["spoolman"])


async def _config_or_503(session: AsyncSession) -> SpoolmanConfig:
    row = await session.get(SpoolmanConfig, 1)
    if row is None or not row.enabled or not row.url:
        raise HTTPException(status_code=503, detail="Spoolman not configured or disabled")
    return row


@router.get(
    "/filaments",
    summary="List Spoolman filaments",
    responses={
        503: {"description": "Spoolman not configured, disabled, or unreachable"},
    },
)
async def get_filaments(session: AsyncSession = Depends(get_session)):
    """Fetch all filament definitions from the configured Spoolman instance."""
    row = await _config_or_503(session)
    try:
        return await spoolman_service.fetch_filaments(row.url, row.api_key)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get(
    "/spools",
    summary="List Spoolman spools",
    responses={
        503: {"description": "Spoolman not configured, disabled, or unreachable"},
    },
)
async def get_spools(session: AsyncSession = Depends(get_session)):
    """Fetch all spool inventory from the configured Spoolman instance."""
    row = await _config_or_503(session)
    try:
        return await spoolman_service.fetch_spools(row.url, row.api_key)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


class FilamentPatchBody(BaseModel):
    orca_profiles: dict[str, list[str]]


@router.patch(
    "/filaments/{filament_id}",
    summary="Update filament OrcaSlicer profiles",
    responses={
        503: {"description": "Spoolman not configured, disabled, or unreachable"},
    },
)
async def patch_filament(
    filament_id: int,
    body: FilamentPatchBody,
    session: AsyncSession = Depends(get_session),
):
    """Write OrcaSlicer profile assignments back to a Spoolman filament's extra fields."""
    row = await _config_or_503(session)
    try:
        return await spoolman_service.patch_filament(
            row.url, row.api_key, filament_id, body.orca_profiles
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
