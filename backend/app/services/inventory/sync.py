"""Generic inventory sync + health for `REMOTE` providers (replaces the old provider-specific loop).

Health lives in the provider's `plugin_configs.state` (`last_sync_at`, `last_attempt_at`, `sync_error`,
`sync_error_code`); the interval and enabled flag are the plugin's own settings. `record_sync` is the single place that
writes them: a successful sync always clears any previously shown error."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from ...plugins.host import plugin_host
from ...plugins.kinds.filament_inventory import KIND, REMOTE, TRACKS_WEIGHT, InventoryProviderError
from . import alerts, outbox, provider

logger = logging.getLogger("app")

_POLL_SECONDS = 60
DEFAULT_INTERVAL_MINUTES = 15


def status(plugin_id: str | None = None) -> dict:
    """Sync health for the status chip / settings page. Never raises; works with no provider at all."""
    pid = plugin_id or plugin_host.slot(KIND)
    cfg_settings, state = (plugin_host.settings(pid), plugin_host.state(pid)) if pid else ({}, {})
    return {
        "enabled": bool(pid and plugin_host.is_enabled(pid)),
        "interval_minutes": int(cfg_settings.get("sync_interval_minutes") or DEFAULT_INTERVAL_MINUTES),
        "last_sync_at": state.get("last_sync_at"),
        "last_attempt_at": state.get("last_attempt_at"),
        "last_error": state.get("sync_error"),
        "last_error_code": state.get("sync_error_code"),
    }


async def record_sync(session: AsyncSession) -> dict:
    """One sync attempt against the active provider; records the outcome. Returns `{material_count, spool_count}`;
    raises `InventoryProviderError` (after recording it) on failure."""
    pid = provider.provider_id()
    if pid is None:
        raise InventoryProviderError("No inventory provider is active", code="NotConfigured")
    now = datetime.now(timezone.utc).isoformat()
    materials = await provider.call("list_materials")
    spools = await provider.call("list_spools") if materials.ok else materials
    if not (materials.ok and spools.ok):
        code, message = provider.describe_failure(spools)
        await plugin_host.record_state(pid, last_attempt_at=now, sync_error=message, sync_error_code=code)
        raise InventoryProviderError(message, code=code, status=getattr(spools.exception, "status", None))
    await plugin_host.record_state(pid, last_attempt_at=now, last_sync_at=now, sync_error=None, sync_error_code=None)
    if provider.has(TRACKS_WEIGHT):
        try:
            # A savepoint, so a problem while alerting (even a DB error) can't poison the sync's own commit.
            async with session.begin_nested():
                await alerts.process(session, pid, spools.value)
        except Exception:
            logger.exception("Low-stock alerting failed; the sync itself succeeded")
    await session.commit()
    return {"material_count": len(materials.value), "spool_count": len(spools.value)}


class InventorySyncLoop:
    """Periodic background sync for the active `REMOTE` provider, paced by its `sync_interval_minutes` setting. Polls
    every _POLL_SECONDS so an interval/provider change takes effect promptly without a restart."""

    def __init__(self) -> None:
        self._session_factory = None
        self._task: asyncio.Task | None = None

    def configure(self, session_factory) -> None:
        self._session_factory = session_factory

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="inventory_sync_loop")

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
                logger.exception("Inventory sync loop tick failed")
            await asyncio.sleep(_POLL_SECONDS)

    async def _tick(self) -> None:
        assert self._session_factory is not None
        if await outbox.has_pending(self._session_factory):       # queued weight updates retry on every poll
            await outbox.flush(self._session_factory)
        if not provider.has(REMOTE):
            return
        st = status(provider.provider_id())
        if st["last_attempt_at"]:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(st["last_attempt_at"])).total_seconds()
            if elapsed < max(1, st["interval_minutes"]) * 60:
                return
        async with self._session_factory() as session:
            try:
                await record_sync(session)
            except Exception:
                pass  # already recorded into the plugin state by record_sync


inventory_sync_loop = InventorySyncLoop()
