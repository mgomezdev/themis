"""Catalog metadata API. Local file APIs and bytes remain owned by the file library."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...auth import require_scope
from ...database import get_session
from ...models import LibraryCatalogEntry
from ...plugins import providers_of
from ...plugins.capabilities.library_source import CAPABILITY
from ...plugins.host import plugin_host
from ...services.library_catalog import CatalogSyncBusy, CatalogSyncError

from ...services.library_sync import synchronize

router = APIRouter(prefix="/api/v1/library", tags=["library"])


class CatalogEntryOut(BaseModel):
    id: str
    provider_plugin_id: str
    source_file_id: str
    source_url: str
    name: str
    missing: bool
    cached_file_id: int | None
    metadata: dict


class CatalogListOut(BaseModel):
    items: list[CatalogEntryOut]
    total: int


class SourceOut(BaseModel):
    plugin_id: str
    name: str
    enabled: bool
    active: bool
    error: str | None
    last_sync_at: str | None


class SourcesOut(BaseModel):
    sources: list[SourceOut]


class SyncOut(BaseModel):
    entries: int
    full_scan: bool


def _entry(row):
    return CatalogEntryOut(
        id=row.id,
        provider_plugin_id=row.provider_plugin_id,
        source_file_id=row.source_file_id,
        source_url=row.source_url,
        name=row.descriptor.get("name", ""),
        missing=row.missing,
        cached_file_id=row.cached_file_id,
        metadata=row.descriptor,
    )


@router.get(
    "/sources",
    response_model=SourcesOut,
    dependencies=[Depends(require_scope("files:read"))],
)
async def sources():
    return SourcesOut(
        sources=[
            SourceOut(
                plugin_id=m.id,
                name=m.name,
                enabled=plugin_host.is_enabled(m.id),
                active=plugin_host.active_for(CAPABILITY, m.id) is not None,
                error=plugin_host.build_error(m.id)
                or plugin_host.state(m.id).get("catalog_error"),
                last_sync_at=plugin_host.state(m.id).get("catalog_synced_at"),
            )
            for m in providers_of(CAPABILITY)
        ]
    )


@router.post(
    "/sources/{plugin_id}/sync",
    response_model=SyncOut,
    dependencies=[Depends(require_scope("files:write"))],
)
async def sync_source(plugin_id: str, session: AsyncSession = Depends(get_session)):
    if plugin_host.active_for(CAPABILITY, plugin_id) is None:
        raise HTTPException(
            409, "Enable and configure this library source before synchronizing"
        )
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    try:
        result = await synchronize(factory, plugin_host, plugin_id)
    except CatalogSyncBusy as exc:
        raise HTTPException(409, str(exc)) from None
    except CatalogSyncError as exc:
        message = plugin_host.redact(plugin_id, str(exc))
        raise HTTPException(502, message) from None
    return result


@router.get(
    "/entries",
    response_model=CatalogListOut,
    dependencies=[Depends(require_scope("files:read"))],
)
async def entries(
    provider: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    filters = [LibraryCatalogEntry.provider_plugin_id == provider] if provider else []
    rows = (
        await session.execute(
            select(LibraryCatalogEntry)
            .where(*filters)
            .order_by(LibraryCatalogEntry.updated_at.desc(), LibraryCatalogEntry.id)
            .offset(offset)
            .limit(limit)
        )
    ).scalars()
    total = (
        await session.execute(
            select(func.count()).select_from(LibraryCatalogEntry).where(*filters)
        )
    ).scalar_one()
    return CatalogListOut(items=[_entry(row) for row in rows], total=total)


@router.get(
    "/entries/{entry_id}",
    response_model=CatalogEntryOut,
    dependencies=[Depends(require_scope("files:read"))],
)
async def entry_detail(entry_id: str, session: AsyncSession = Depends(get_session)):
    row = await session.get(LibraryCatalogEntry, entry_id)
    if row is None:
        raise HTTPException(404, "Catalog entry not found")
    return _entry(row)


class MaterializationOut(BaseModel):
    file_id: int
    fresh: bool
    warning: str | None


@router.post(
    "/entries/{entry_id}/materialize",
    response_model=MaterializationOut,
    dependencies=[Depends(require_scope("files:write"))],
)
async def materialize_entry(
    entry_id: str, session: AsyncSession = Depends(get_session)
):
    from ...services.library_materialization import (
        CatalogEntryNotFound,
        MaterializationError,
        materialize,
    )

    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    try:
        return await materialize(factory, plugin_host, entry_id)
    except CatalogEntryNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except CatalogSyncBusy:
        raise HTTPException(
            409, "A source operation is already running; retry shortly"
        ) from None
    except (MaterializationError, CatalogSyncError) as exc:
        raise HTTPException(502, str(exc)) from None


class FileSourceOriginOut(BaseModel):
    file_id: int
    entry_id: str
    provider_plugin_id: str
    source_url: str
    revision: str | None
    downloaded_at: str
    metadata: dict
    is_latest: bool
    source_missing: bool


class FileSourceOriginResponse(BaseModel):
    origin: FileSourceOriginOut | None


@router.get(
    "/files/{file_id}/origin",
    response_model=FileSourceOriginResponse,
    dependencies=[Depends(require_scope("files:read"))],
)
async def file_source_origin(
    file_id: int, session: AsyncSession = Depends(get_session)
):
    from ...models import LibrarySourceRevision, UploadedFile

    if await session.get(UploadedFile, file_id) is None:
        raise HTTPException(404, "Local file not found")
    result = (
        await session.execute(
            select(LibrarySourceRevision, LibraryCatalogEntry)
            .join(
                LibraryCatalogEntry,
                LibraryCatalogEntry.id == LibrarySourceRevision.entry_id,
            )
            .where(LibrarySourceRevision.file_id == file_id)
        )
    ).first()
    if result is None:
        return FileSourceOriginResponse(origin=None)
    revision, entry = result
    return FileSourceOriginResponse(
        origin=FileSourceOriginOut(
            file_id=file_id,
            entry_id=entry.id,
            provider_plugin_id=entry.provider_plugin_id,
            source_url=revision.descriptor["source_url"],
            revision=revision.revision,
            downloaded_at=revision.downloaded_at,
            metadata=revision.descriptor,
            is_latest=entry.cached_file_id == file_id,
            source_missing=entry.missing,
        )
    )
