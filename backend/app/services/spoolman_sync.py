"""Tracks Spoolman sync health for the status indicator and the Spoolman
settings page: records the outcome of every sync attempt (manual "Sync now" or
the periodic background loop) onto the SpoolmanConfig row, and runs the
periodic loop itself.

`record_sync` is the single place that writes last_sync_at/last_attempt_at/
last_sync_error(_code) — a successful sync always clears any previously
displayed error."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import SpoolmanConfig
from . import spool_alerts, spoolman_service

logger = logging.getLogger("app")

_POLL_SECONDS = 60


def _describe_error(e: Exception) -> tuple[str, str]:
    if isinstance(e, httpx.HTTPStatusError):
        return str(e.response.status_code), str(e)
    if isinstance(e, httpx.RequestError):
        return type(e).__name__, str(e)
    return type(e).__name__, str(e)


async def record_sync(session: AsyncSession, row: SpoolmanConfig) -> dict:
    """Runs one sync attempt against `row`'s Spoolman instance and persists the
    outcome onto `row` (caller must have already loaded it in `session`).
    Returns {filament_count, spool_count} on success; re-raises on failure
    after recording the error."""
    now = datetime.now(timezone.utc).isoformat()
    row.last_attempt_at = now
    try:
        filaments = await spoolman_service.fetch_filaments(row.url, row.api_key)
        spools = await spoolman_service.fetch_spools(row.url, row.api_key)
    except Exception as e:
        row.last_sync_error_code, row.last_sync_error = _describe_error(e)
        await session.commit()
        raise
    row.last_sync_at = now
    row.last_sync_error = None
    row.last_sync_error_code = None
    try:
        # A savepoint, so a problem while alerting (even a DB error) can't poison the sync's own commit.
        async with session.begin_nested():
            await spool_alerts.process(session, row, spools)
    except Exception:
        logger.exception("Low-stock alerting failed; the sync itself succeeded")
    await session.commit()
    return {"filament_count": len(filaments), "spool_count": len(spools)}


class SpoolmanSyncLoop:
    """Periodic background sync, paced by SpoolmanConfig.sync_interval_minutes.
    Polls every _POLL_SECONDS so an interval/enabled change takes effect
    promptly without needing a restart."""

    def __init__(self) -> None:
        self._session_factory = None
        self._task: asyncio.Task | None = None

    def configure(self, session_factory) -> None:
        self._session_factory = session_factory

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="spoolman_sync_loop")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Spoolman sync loop tick failed")
            await asyncio.sleep(_POLL_SECONDS)

    async def _tick(self) -> None:
        assert self._session_factory is not None
        async with self._session_factory() as session:
            row = await session.get(SpoolmanConfig, 1)
            if row is None or not row.enabled or not row.url:
                return
            interval_s = max(1, row.sync_interval_minutes) * 60
            if row.last_attempt_at:
                elapsed = (
                    datetime.now(timezone.utc) - datetime.fromisoformat(row.last_attempt_at)
                ).total_seconds()
                if elapsed < interval_s:
                    return
            try:
                await record_sync(session, row)
            except Exception:
                pass  # already recorded onto row by record_sync


spoolman_sync_loop = SpoolmanSyncLoop()
