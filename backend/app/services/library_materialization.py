"""Publish source bytes as ordinary local files; never overwrite a pinned revision."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import PurePosixPath
from uuid import uuid4

from sqlalchemy import select

from .. import config
from ..models import LibraryCatalogEntry, LibrarySourceRevision, UploadedFile
from ..plugins.capabilities.library_source import LIBRARY_SOURCE, SourceDownload
from .artifact_store import PART_SUFFIX, validate_key
from .library_catalog import CatalogSyncError, _lease, _renew, _validate
from .library_scanner import LibraryScanner, folder_of
from .library_store import get_filecache_store, get_library_store

MAX_BYTES = 512 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 600


class MaterializationError(ValueError):
    pass


class CatalogEntryNotFound(MaterializationError):
    pass


async def _io(fn, *args, **kwargs):
    # Do not close/delete a file while a canceled worker is still writing it.
    task = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


async def _cached(factory, entry_id, store):
    async with factory() as session:
        entry = await session.get(LibraryCatalogEntry, entry_id)
        if entry is None:
            raise CatalogEntryNotFound("Catalog entry not found")
        cached = (
            await session.get(UploadedFile, entry.cached_file_id)
            if entry.cached_file_id
            else None
        )
        if (
            cached
            and not cached.missing
            and await _io(store.exists, cached.relative_path)
        ):
            return cached.id
    return None


async def _remote(factory, host, provider, owner, fn):
    from . import library_catalog

    task = asyncio.create_task(
        host.call_for(LIBRARY_SOURCE, provider, fn, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    )
    try:
        while not task.done():
            done, _ = await asyncio.wait(
                {task}, timeout=library_catalog.LEASE_SECONDS / 3
            )
            if not done:
                async with factory() as session:
                    await _renew(session, provider, owner)
                    await session.commit()
        result = task.result()
        if not result.ok:
            raise MaterializationError(result.error or "Library source is unavailable")
        return result.value
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def materialize(factory, host, entry_id: str, *, max_bytes=MAX_BYTES):
    store = get_library_store()
    async with factory() as session:
        entry = await session.get(LibraryCatalogEntry, entry_id)
        if entry is None:
            raise CatalogEntryNotFound("Catalog entry not found")
        provider, source_id, source_url = (
            entry.provider_plugin_id,
            entry.source_file_id,
            entry.source_url,
        )
    owner = str(uuid4())
    try:
        # Serialize downloads with source sync too: metadata and cache pointers must
        # not race across processes. This intentionally serializes a provider's imports.
        async with _lease(factory, provider, owner):
            return await _materialize(
                factory,
                host,
                entry_id,
                provider,
                source_id,
                source_url,
                owner,
                store,
                max_bytes,
            )
    except (MaterializationError, CatalogSyncError) as exc:
        cached = await _cached(factory, entry_id, store)
        if cached is None:
            raise
        return {
            "file_id": cached,
            "fresh": False,
            "warning": f"Using the last-known local copy. {exc}",
        }


async def _materialize(
    factory, host, entry_id, provider, source_id, source_url, owner, store, max_bytes
):
    scratch = f".source-{uuid4().hex}{PART_SUFFIX}"
    key, keep = None, False
    try:

        async def download(source):
            descriptor = await source.describe(source_id)
            _validate(descriptor)
            if descriptor.revision is not None and (
                not isinstance(descriptor.revision, str)
                or len(descriptor.revision) > 1024
            ):
                raise MaterializationError("Source returned an invalid revision")
            if (
                descriptor.source_file_id != source_id
                or descriptor.source_url != source_url
            ):
                raise MaterializationError("Source described a different file identity")
            # Source names never control folders. Reject platform-dependent paths.
            filename = descriptor.name
            if (
                PurePosixPath(filename).name != filename
                or "\\" in filename
                or len(filename.encode("utf-8")) > 180
            ):
                raise MaterializationError("Source returned an invalid filename")
            validate_key(filename)
            if PurePosixPath(filename).suffix.lower() not in {".stl", ".3mf", ".gcode"}:
                raise MaterializationError(
                    "Only .3mf, .stl and .gcode files can be materialized"
                )
            if descriptor.size_bytes is not None and descriptor.size_bytes > max_bytes:
                raise MaterializationError(
                    "Source file exceeds the local download limit"
                )
            async with source.download(
                source_id, revision=descriptor.revision
            ) as stream:
                if not isinstance(stream, SourceDownload):
                    raise MaterializationError(
                        "Source returned an invalid download stream"
                    )
                if stream.revision is not None and (
                    not isinstance(stream.revision, str) or len(stream.revision) > 1024
                ):
                    raise MaterializationError(
                        "Source returned an invalid download revision"
                    )
                if stream.size_bytes is not None and (
                    isinstance(stream.size_bytes, bool)
                    or not isinstance(stream.size_bytes, int)
                    or not 0 <= stream.size_bytes <= max_bytes
                ):
                    raise MaterializationError(
                        "Source download exceeds the local byte limit or has an invalid size"
                    )
                if (
                    descriptor.revision is not None
                    and stream.revision != descriptor.revision
                ):
                    raise MaterializationError(
                        "Source file changed during download; retry"
                    )
                writer_context = store.writer(scratch)
                enter = asyncio.create_task(asyncio.to_thread(writer_context.__enter__))
                try:
                    writer = await asyncio.shield(enter)
                except asyncio.CancelledError as exc:
                    outcome = await asyncio.gather(enter, return_exceptions=True)
                    if not isinstance(outcome[0], BaseException):
                        await _io(
                            writer_context.__exit__, type(exc), exc, exc.__traceback__
                        )
                    raise
                try:
                    async for chunk in stream.chunks:
                        if not isinstance(chunk, bytes):
                            raise MaterializationError(
                                "Source returned invalid file bytes"
                            )
                        if writer.size + len(chunk) > max_bytes:
                            raise MaterializationError(
                                "Source file exceeds the local download limit"
                            )
                        await _io(writer.write, chunk)
                    if (
                        stream.size_bytes is not None
                        and writer.size != stream.size_bytes
                    ) or (
                        descriptor.size_bytes is not None
                        and writer.size != descriptor.size_bytes
                    ):
                        raise MaterializationError("Source download was incomplete")
                except BaseException as exc:
                    await _io(
                        writer_context.__exit__, type(exc), exc, exc.__traceback__
                    )
                    raise
                else:
                    await _io(writer_context.__exit__, None, None, None)
            return descriptor, stream.revision, writer.info

        descriptor, revision, info = await _remote(
            factory, host, provider, owner, download
        )
        snapshot = json.loads(json.dumps(asdict(descriptor)))
        async with factory() as session:
            await _renew(session, provider, owner)
            # Same bytes AND origin/terms snapshot may reuse the existing local ID.
            candidates = (
                await session.execute(
                    select(LibrarySourceRevision, UploadedFile)
                    .join(
                        UploadedFile, UploadedFile.id == LibrarySourceRevision.file_id
                    )
                    .where(
                        LibrarySourceRevision.entry_id == entry_id,
                        UploadedFile.content_hash == info.sha256,
                    )
                )
            ).all()
            for previous, file in candidates:
                if (
                    previous.revision == revision
                    and previous.descriptor == snapshot
                    and not file.missing
                    and await _io(store.exists, file.relative_path)
                ):
                    entry = await session.get(LibraryCatalogEntry, entry_id)
                    entry.cached_file_id, entry.descriptor, entry.missing = (
                        file.id,
                        snapshot,
                        False,
                    )
                    await session.commit()
                    return {"file_id": file.id, "fresh": True, "warning": None}
            key = f"Sources/{entry_id}/{uuid4().hex}/{descriptor.name}"
            placed = await _io(store.move, scratch, key)
            file = UploadedFile(
                original_filename=descriptor.name,
                relative_path=key,
                folder=folder_of(key),
                size_bytes=placed.size,
                content_hash=info.sha256,
                mtime=placed.mtime,
                plates=[],
                missing=False,
                uploaded_at=datetime.now(timezone.utc).isoformat(),
            )
            session.add(file)
            await session.flush()
            scanner = LibraryScanner(
                session,
                config.get_library_dir(),
                config.get_filecache_dir(),
                library_store=store,
                filecache_store=get_filecache_store(),
            )
            file.plates = await _io(scanner.parse_plates, key, file.id)
            session.add(
                LibrarySourceRevision(
                    file_id=file.id,
                    entry_id=entry_id,
                    revision=revision,
                    descriptor=snapshot,
                    downloaded_at=file.uploaded_at,
                )
            )
            entry = await session.get(LibraryCatalogEntry, entry_id)
            entry.cached_file_id, entry.descriptor, entry.missing = (
                file.id,
                snapshot,
                False,
            )
            # Commit cancellation has an uncertain outcome: retain complete bytes;
            # the filesystem scanner can recover an unregistered complete artifact.
            await _renew(session, provider, owner)
            keep = True
            await session.commit()
            return {"file_id": file.id, "fresh": True, "warning": None}
    finally:
        await _io(store.delete, scratch, missing_ok=True)
        if key is not None and not keep:
            await _io(store.delete, key, missing_ok=True)
