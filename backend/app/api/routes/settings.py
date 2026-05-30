from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import QueueConfig, SpoolmanConfig
from ...services import spoolman_service

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


class QueueConfigOut(BaseModel):
    check_interval_minutes: int


class QueueConfigIn(BaseModel):
    check_interval_minutes: int


async def _get_or_create_queue(session: AsyncSession) -> QueueConfig:
    row = await session.get(QueueConfig, 1)
    if row is None:
        row = QueueConfig(id=1, check_interval_minutes=5)
        session.add(row)
        await session.flush()
    return row


@router.get("/queue", response_model=QueueConfigOut)
async def get_queue_config(session: AsyncSession = Depends(get_session)):
    return await _get_or_create_queue(session)


@router.put("/queue", response_model=QueueConfigOut)
async def update_queue_config(
    body: QueueConfigIn,
    session: AsyncSession = Depends(get_session),
):
    row = await _get_or_create_queue(session)
    row.check_interval_minutes = max(1, body.check_interval_minutes)
    await session.commit()
    await session.refresh(row)
    return row


class SpoolmanConfigOut(BaseModel):
    enabled: bool
    url: str | None
    api_key: str | None


class SpoolmanConfigIn(BaseModel):
    enabled: bool | None = None
    url: str | None = None
    api_key: str | None = None


async def _get_or_create(session: AsyncSession) -> SpoolmanConfig:
    row = await session.get(SpoolmanConfig, 1)
    if row is None:
        row = SpoolmanConfig(id=1, enabled=False)
        session.add(row)
        await session.flush()
    return row


@router.get("/spoolman", response_model=SpoolmanConfigOut)
async def get_spoolman_config(session: AsyncSession = Depends(get_session)):
    return await _get_or_create(session)


@router.put("/spoolman", response_model=SpoolmanConfigOut)
async def update_spoolman_config(
    body: SpoolmanConfigIn,
    session: AsyncSession = Depends(get_session),
):
    row = await _get_or_create(session)
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.url is not None:
        row.url = body.url or None
    if body.api_key is not None:
        row.api_key = body.api_key or None
    await session.commit()
    await session.refresh(row)
    return row


@router.post("/spoolman/test")
async def test_spoolman_connection(
    body: SpoolmanConfigIn,
    session: AsyncSession = Depends(get_session),
):
    url = body.url
    api_key = body.api_key
    if not url:
        row = await _get_or_create(session)
        url = row.url
        if api_key is None:
            api_key = row.api_key
    if not url:
        return {"ok": False, "message": "No URL configured"}
    try:
        info = await spoolman_service.test_connection(url, api_key)
        return {"ok": True, "version": info.get("version", "unknown")}
    except Exception as e:
        return {"ok": False, "message": str(e)}
