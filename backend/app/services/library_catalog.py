"""Metadata sync for routed library sources; no source network/format logic in core."""
from __future__ import annotations

import time
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4
from urllib.parse import urlsplit

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.sqlite import insert

from ..models import LibraryCatalogEntry, LibraryCatalogSyncLease
from ..plugins.capabilities.library_source import LIBRARY_SOURCE, CatalogPage, SourceCreator, SourceFile, SourceFileMissing

LEASE_SECONDS = 120.0
PAGE_TIMEOUT_SECONDS = 600.0


class CatalogSyncError(ValueError):
    pass


class CatalogSyncBusy(CatalogSyncError):
    pass


def _validate(entry):
    if not isinstance(entry, SourceFile) or not all(isinstance(v, str) and v.strip() for v in
            (entry.source_file_id, entry.source_url, entry.name)):
        raise CatalogSyncError("Source returned an incomplete file descriptor")
    if len(entry.source_file_id) > 1024 or len(entry.source_url) > 2048 or len(entry.name) > 512:
        raise CatalogSyncError("Source returned an oversized file descriptor")
    try:
        url = urlsplit(entry.source_url)
        url.port
    except ValueError:
        raise CatalogSyncError("Source returned an invalid canonical page URL") from None
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise CatalogSyncError("Source returned an invalid canonical page URL")
    if entry.size_bytes is not None and (not isinstance(entry.size_bytes, int) or isinstance(entry.size_bytes, bool) or entry.size_bytes < 0):
        raise CatalogSyncError("Source returned an invalid file size")

    for field in (
        "media_type",
        "revision",
        "license_code",
        "license_url",
        "preview_url",
    ):
        value = getattr(entry, field)
        if value is not None and not isinstance(value, str):
            raise CatalogSyncError(f"Source returned invalid {field} metadata")
    if not isinstance(entry.collections, (tuple, list)) or not all(
        isinstance(c, str) for c in entry.collections
    ):
        raise CatalogSyncError("Source returned invalid collection metadata")
    creator = entry.creator
    if creator is not None and (
        not isinstance(creator, SourceCreator)
        or not isinstance(creator.id, str)
        or not creator.id.strip()
        or not isinstance(creator.name, str)
        or not isinstance(creator.links, (tuple, list))
        or not all(isinstance(link, str) for link in creator.links)
    ):
        raise CatalogSyncError("Source returned invalid creator metadata")


async def _upsert(factory, provider, entry, sweep):
    _validate(entry)
    async with factory() as session:
        await _renew(session, provider, sweep)
        values = dict(id=str(uuid4()), provider_plugin_id=provider, source_file_id=entry.source_file_id,
            source_url=entry.source_url, descriptor=asdict(entry), last_seen_sweep=sweep, missing=False,
            updated_at=datetime.now(timezone.utc).isoformat())
        await session.execute(insert(LibraryCatalogEntry).values(**values).on_conflict_do_nothing())
        # URL dedupe can point at another source's entry; never replace its owner.
        rows = list((await session.execute(select(LibraryCatalogEntry).where(or_(
            (LibraryCatalogEntry.provider_plugin_id == provider) & (LibraryCatalogEntry.source_file_id == entry.source_file_id),
            LibraryCatalogEntry.source_url == entry.source_url)))).scalars())
        if len(rows) != 1:
            raise CatalogSyncError("Source ID and URL refer to different catalog entries")
        row = rows[0]
        if row.provider_plugin_id == provider:
            for key in ('source_file_id', 'source_url', 'descriptor', 'last_seen_sweep', 'missing', 'updated_at'):
                setattr(row, key, values[key])
        await session.commit()
        return row.id


@asynccontextmanager
async def _lease(factory, provider, owner):
    now = time.time()
    async with factory() as session:
        await session.execute(insert(LibraryCatalogSyncLease).values(
            provider_plugin_id=provider, owner=owner, expires_at=0).on_conflict_do_nothing())
        result = await session.execute(update(LibraryCatalogSyncLease).where(
            LibraryCatalogSyncLease.provider_plugin_id == provider,
            LibraryCatalogSyncLease.expires_at <= now).values(owner=owner, expires_at=now + LEASE_SECONDS))
        if result.rowcount != 1:
            raise CatalogSyncBusy("A catalog sync is already running for this provider")
        await session.commit()
    try:
        yield
    finally:
        async with factory() as session:
            await session.execute(delete(LibraryCatalogSyncLease).where(
                LibraryCatalogSyncLease.provider_plugin_id == provider, LibraryCatalogSyncLease.owner == owner))
            await session.commit()


async def _renew(session, provider, owner):
    now = time.time()
    result = await session.execute(update(LibraryCatalogSyncLease).where(
        LibraryCatalogSyncLease.provider_plugin_id == provider, LibraryCatalogSyncLease.owner == owner,
        LibraryCatalogSyncLease.expires_at > now).values(expires_at=now + LEASE_SECONDS))
    if result.rowcount != 1:
        raise CatalogSyncError("Catalog sync lease expired; no missing entries were marked")


async def _read_page(factory, host, provider, cursor, sweep):
    # A source may need many detail reads to build one page. Keep its DB lease
    # alive without holding a SQL transaction during network I/O.
    task = asyncio.create_task(host.call_for(LIBRARY_SOURCE, provider,
        lambda p: p.list_catalog(cursor=cursor), timeout=PAGE_TIMEOUT_SECONDS))
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=LEASE_SECONDS / 3)
            if not done:
                async with factory() as session:
                    await _renew(session, provider, sweep)
                    await session.commit()
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def sync_catalog(factory, host, provider: str, *, max_pages: int = 10000) -> dict:
    """Upsert metadata page by page; mark missing ONLY after a fully successful full sweep.

    Prior catalog/cached bytes survive failed, canceled, partial and incremental
    reads. Page cursors are opaque and cycles are rejected before calling again.
    """
    if max_pages < 1:
        raise CatalogSyncError("Catalog page limit must be positive")
    sweep = str(uuid4())
    async with _lease(factory, provider, sweep):
        cursor, cursors, ids = None, set(), set()
        full_scan, confirm_missing = True, False
        for _ in range(max_pages):
            async with factory() as session:
                await _renew(session, provider, sweep)
                await session.commit()
            result = await _read_page(factory, host, provider, cursor, sweep)
            if not result.ok:
                raise CatalogSyncError(result.error or "Source is unavailable")
            page = result.value
            if not isinstance(page, CatalogPage):
                raise CatalogSyncError("Source returned an invalid catalog page")
            if not isinstance(page.entries, (tuple, list)) or len(page.entries) > 5000:
                raise CatalogSyncError("Source returned an invalid or oversized catalog page")
            full_scan = full_scan and page.full_scan is True
            confirm_missing = confirm_missing or page.confirm_missing
            for entry in page.entries:
                ids.add(await _upsert(factory, provider, entry, sweep))
            cursor = page.next_cursor
            if cursor is None:
                if full_scan:
                    if confirm_missing:
                        async with factory() as session:
                            unseen = list((await session.execute(select(LibraryCatalogEntry.source_file_id).where(
                                LibraryCatalogEntry.provider_plugin_id == provider,
                                LibraryCatalogEntry.last_seen_sweep != sweep))).scalars())
                        for source_id in unseen:
                            async with factory() as session:
                                await _renew(session, provider, sweep)
                                await session.commit()
                            found = await host.call_for(LIBRARY_SOURCE, provider, lambda p: p.describe(source_id), record=False)
                            if found.ok:
                                _validate(found.value)
                                if found.value.source_file_id != source_id:
                                    raise CatalogSyncError("Source described a different file identity")
                                ids.add(await _upsert(factory, provider, found.value, sweep))
                            elif not isinstance(found.exception, SourceFileMissing):
                                raise CatalogSyncError(found.error or "Could not confirm a missing source file")
                    async with factory() as session:
                        await _renew(session, provider, sweep)
                        await session.execute(update(LibraryCatalogEntry).where(
                            LibraryCatalogEntry.provider_plugin_id == provider,
                            LibraryCatalogEntry.last_seen_sweep != sweep).values(missing=True))
                        await session.commit()
                return {"entries": len(ids), "full_scan": full_scan}
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise CatalogSyncError("Source returned a repeated or invalid page cursor")
            cursors.add(cursor)
        raise CatalogSyncError("Source exceeded the catalog page limit")
