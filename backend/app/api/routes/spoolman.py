from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import SpoolmanConfig
from ...services import spoolman_service
from ...services.spoolman_sync import record_sync

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
    dependencies=[Depends(require_scope("spoolman:read"))],
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
    dependencies=[Depends(require_scope("spoolman:read"))],
)
async def get_spools(session: AsyncSession = Depends(get_session)):
    """Fetch all spool inventory from the configured Spoolman instance."""
    row = await _config_or_503(session)
    try:
        return await spoolman_service.fetch_spools(row.url, row.api_key)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


class SyncNowResponse(BaseModel):
    filament_count: int
    spool_count: int


@router.post(
    "/sync-now",
    summary="Manually sync filaments and spools from Spoolman",
    responses={
        503: {"description": "Spoolman not configured, disabled, or unreachable"},
    },
    dependencies=[Depends(require_scope("spoolman:read"))],
)
async def sync_now(session: AsyncSession = Depends(get_session)):
    """Re-fetch filaments and spools from Spoolman, confirming the connection.
    Records the outcome (success clears any previously shown error) so the
    status indicator and Spoolman settings page reflect this attempt too."""
    row = await _config_or_503(session)
    try:
        result = await record_sync(session, row)
        return SyncNowResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


class SyncStatusResponse(BaseModel):
    enabled: bool
    interval_minutes: int
    last_sync_at: str | None
    last_attempt_at: str | None
    last_error: str | None
    last_error_code: str | None


@router.get(
    "/sync-status",
    summary="Spoolman sync health for the status indicator and settings page",
    response_model=SyncStatusResponse,
    dependencies=[Depends(require_scope("spoolman:read"))],
)
async def get_sync_status(session: AsyncSession = Depends(get_session)):
    """Never 503s, even when Spoolman is disabled/unconfigured — callers use
    `enabled` to decide whether to show anything at all."""
    row = await session.get(SpoolmanConfig, 1)
    if row is None:
        return SyncStatusResponse(
            enabled=False, interval_minutes=15,
            last_sync_at=None, last_attempt_at=None,
            last_error=None, last_error_code=None,
        )
    return SyncStatusResponse(
        enabled=row.enabled,
        interval_minutes=row.sync_interval_minutes,
        last_sync_at=row.last_sync_at,
        last_attempt_at=row.last_attempt_at,
        last_error=row.last_sync_error,
        last_error_code=row.last_sync_error_code,
    )


class FilamentPatchBody(BaseModel):
    orca_profiles: dict[str, list[str]]


@router.patch(
    "/filaments/{filament_id}",
    summary="Update filament OrcaSlicer profiles",
    responses={
        503: {"description": "Spoolman not configured, disabled, or unreachable"},
    },
    dependencies=[Depends(require_scope("spoolman:write"))],
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
