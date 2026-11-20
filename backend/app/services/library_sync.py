"""Periodic, metadata-only catalog synchronization for active routed sources."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert

from ..models import LibraryCatalogSchedule
from ..plugins import providers_of
from ..plugins.capabilities.library_source import CAPABILITY
from .library_catalog import CatalogSyncBusy, CatalogSyncError, sync_catalog

POLL_SECONDS = 60
DEFAULT_INTERVAL_MINUTES = 60
logger = logging.getLogger("app")


def interval_seconds(host, provider):
    minutes = host.settings(provider).get(
        "sync_interval_minutes", DEFAULT_INTERVAL_MINUTES
    )
    if (
        isinstance(minutes, bool)
        or not isinstance(minutes, int)
        or not 0 <= minutes <= 10080
    ):
        minutes = DEFAULT_INTERVAL_MINUTES
    return minutes * 60


async def _defer(factory, provider, due):
    async with factory() as session:
        await session.execute(
            insert(LibraryCatalogSchedule)
            .values(provider_plugin_id=provider, next_due_at=due)
            .on_conflict_do_update(
                index_elements=["provider_plugin_id"], set_={"next_due_at": due}
            )
        )
        await session.commit()


async def synchronize(factory, host, provider):
    """One health-recording path for manual and scheduled sync; no source writes."""
    try:
        result = await sync_catalog(factory, host, provider)
    except CatalogSyncBusy:
        raise
    except CatalogSyncError as exc:
        message = host.redact(provider, str(exc))
        await host.record_state(provider, catalog_error=message)
        raise CatalogSyncError(message) from None
    await host.record_state(
        provider,
        catalog_error=None,
        catalog_synced_at=datetime.now(timezone.utc).isoformat(),
    )
    # A successful manual sync also postpones the next automatic sweep.
    await _defer(factory, provider, time.time() + interval_seconds(host, provider))
    return result


async def sync_due(factory, host, provider):
    interval = interval_seconds(host, provider)
    if interval == 0 or host.active_for(CAPABILITY, provider) is None:
        return False
    now = time.time()
    claimed_due = now + interval
    async with factory() as session:
        await session.execute(
            insert(LibraryCatalogSchedule)
            .values(provider_plugin_id=provider, next_due_at=0)
            .on_conflict_do_nothing()
        )
        claim = await session.execute(
            update(LibraryCatalogSchedule)
            .where(
                LibraryCatalogSchedule.provider_plugin_id == provider,
                LibraryCatalogSchedule.next_due_at <= now,
            )
            .values(next_due_at=claimed_due)
        )
        await session.commit()
        if claim.rowcount != 1:
            return False
    try:
        await synchronize(factory, host, provider)
    except CatalogSyncBusy:
        # Another source operation owns its lease. Retry on the next poll, but
        # don't overwrite a later successful manual sync's due timestamp.
        async with factory() as session:
            await session.execute(
                update(LibraryCatalogSchedule)
                .where(
                    LibraryCatalogSchedule.provider_plugin_id == provider,
                    LibraryCatalogSchedule.next_due_at == claimed_due,
                )
                .values(next_due_at=now + POLL_SECONDS)
            )
            await session.commit()
        return False
    except CatalogSyncError:
        pass  # health already recorded; durable claim backs off even after failure/crash.
    return True


class LibrarySyncLoop:
    def __init__(self):
        self._task = None

    async def start(self, factory, host):
        if self._task is None:
            self._task = asyncio.create_task(
                self._loop(factory, host), name="library-catalog-sync"
            )

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run_source(self, factory, host, provider):
        try:
            await sync_due(factory, host, provider)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Library catalog scheduled sync failed for %s", provider)

    async def _loop(self, factory, host):
        workers = {}
        try:
            while True:
                # Each provider has its own retained task. A slow catalog cannot
                # hold up another source's poll, while DB claims coordinate processes.
                for manifest in providers_of(CAPABILITY):
                    task = workers.get(manifest.id)
                    if task is None or task.done():
                        workers[manifest.id] = asyncio.create_task(
                            self._run_source(factory, host, manifest.id),
                            name=f"library-sync-{manifest.id}",
                        )
                await asyncio.sleep(POLL_SECONDS)
        finally:
            for task in workers.values():
                task.cancel()
            await asyncio.gather(*workers.values(), return_exceptions=True)


library_sync_loop = LibrarySyncLoop()
