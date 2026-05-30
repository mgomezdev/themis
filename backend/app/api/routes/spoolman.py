from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
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


@router.get("/filaments")
async def get_filaments(session: AsyncSession = Depends(get_session)):
    row = await _config_or_503(session)
    try:
        return await spoolman_service.fetch_filaments(row.url, row.api_key)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/spools")
async def get_spools(session: AsyncSession = Depends(get_session)):
    row = await _config_or_503(session)
    try:
        return await spoolman_service.fetch_spools(row.url, row.api_key)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
