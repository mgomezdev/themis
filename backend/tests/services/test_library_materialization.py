from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import asyncio
import pytest
from sqlalchemy import select

from app import config
from app.models import Job, LibraryCatalogEntry, LibrarySourceRevision, UploadedFile
from app.plugins.capabilities.library_source import SourceDownload, SourceFile
from app.services.library_materialization import (
    CatalogEntryNotFound,
    MaterializationError,
    materialize,
)
from app.services.library_store import get_library_store


class Source:
    descriptor = SourceFile("one", "part.stl", "https://source.test/one", revision="v1")
    data = b"solid part\nendsolid part\n"
    error = None
    closed = False
    hold = None
    entered = None

    async def describe(self, source_id):
        if self.error:
            raise self.error
        return self.descriptor

    @asynccontextmanager
    async def download(self, source_id, revision=None):
        async def chunks():
            yield self.data[:3]
            if self.entered:
                self.entered.set()
            if self.hold:
                await self.hold.wait()
            yield self.data[3:]

        try:
            yield SourceDownload(chunks(), self.descriptor.revision, len(self.data))
        finally:
            self.closed = True


class Host:
    def __init__(self):
        self.source = Source()

    async def call_for(self, handle, provider, fn, **kwargs):
        try:
            return SimpleNamespace(ok=True, value=await fn(self.source))
        except ValueError as exc:
            return SimpleNamespace(ok=False, error=str(exc))


@pytest.fixture
async def catalog(session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_library_dir", lambda: tmp_path / "library")
    monkeypatch.setattr(config, "get_filecache_dir", lambda: tmp_path / "filecache")
    host = Host()
    async with session_factory() as session:
        session.add(
            LibraryCatalogEntry(
                id="entry",
                provider_plugin_id="source",
                source_file_id="one",
                source_url=host.source.descriptor.source_url,
                descriptor={},
                last_seen_sweep="sweep",
                updated_at="now",
            )
        )
        await session.commit()
    return host


async def test_local_materialization_dedup_attribution_and_historical_revisions(
    session_factory, catalog
):
    first = await materialize(session_factory, catalog, "entry")
    assert first["fresh"] and first["warning"] is None
    async with session_factory() as session:
        old = await session.get(UploadedFile, first["file_id"])
        assert old.plates[0]["plate_number"] == 1 and not old.eligibility_known
        assert (
            await session.get(LibraryCatalogEntry, "entry")
        ).cached_file_id == old.id
        revision = await session.get(LibrarySourceRevision, old.id)
        assert (
            revision.revision == "v1"
            and revision.descriptor["source_url"] == "https://source.test/one"
        )
    assert await materialize(session_factory, catalog, "entry") == first
    async with session_factory() as session:
        assert len(list((await session.execute(select(UploadedFile))).scalars())) == 1
    async with session_factory() as session:
        job = Job(uploaded_file_id=first["file_id"], created_at="now", updated_at="now")
        session.add(job)
        await session.commit()
        job_id = job.id
    old_bytes = catalog.source.data
    catalog.source.descriptor = replace(
        catalog.source.descriptor, revision="v2", license_code="CC-BY-4.0"
    )
    catalog.source.data = b"solid changed\nendsolid changed\n"
    second = await materialize(session_factory, catalog, "entry")
    assert first["file_id"] != second["file_id"]
    async with session_factory() as session:
        assert (await session.get(Job, job_id)).uploaded_file_id == first["file_id"]
    with get_library_store().open(old.relative_path) as content:
        assert content.read() == old_bytes
    async with session_factory() as session:
        assert (await session.get(LibrarySourceRevision, old.id)).descriptor[
            "license_code"
        ] is None
    catalog.source.error = ValueError("Source offline")
    cached = await materialize(session_factory, catalog, "entry")
    assert cached["file_id"] == second["file_id"] and not cached["fresh"]
    assert "last-known" in cached["warning"] and "offline" in cached["warning"]
    assert catalog.source.closed


@pytest.mark.parametrize(
    "filename", ["../escape.stl", "/root.stl", "C:\\bad.stl", "bad.exe"]
)
async def test_unsafe_or_unsupported_filenames_publish_nothing(
    session_factory, catalog, filename
):
    catalog.source.descriptor = replace(catalog.source.descriptor, name=filename)
    with pytest.raises(MaterializationError):
        await materialize(session_factory, catalog, "entry")
    assert not list(get_library_store().list())
    async with session_factory() as session:
        assert (await session.get(LibraryCatalogEntry, "entry")).cached_file_id is None


async def test_unknown_length_stream_overflow_closes_without_partial_artifacts(
    session_factory, catalog
):
    @asynccontextmanager
    async def download(*args, **kwargs):
        async def chunks():
            yield b"123"
            yield b"456"

        try:
            yield SourceDownload(chunks(), revision="v1")
        finally:
            catalog.source.closed = True

    catalog.source.download = download
    with pytest.raises(MaterializationError, match="limit"):
        await materialize(session_factory, catalog, "entry", max_bytes=4)
    assert catalog.source.closed and not list(get_library_store().list())
    assert not list(config.get_library_dir().rglob("*.part"))


async def test_cancellation_closes_stream_and_releases_lease_for_retry(
    session_factory, catalog
):
    catalog.source.entered, catalog.source.hold = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(materialize(session_factory, catalog, "entry"))
    await catalog.source.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert catalog.source.closed and not list(get_library_store().list())
    assert not list(config.get_library_dir().rglob("*.part"))
    catalog.source.hold = None
    assert (await materialize(session_factory, catalog, "entry"))["fresh"]


async def test_missing_cache_cannot_masquerade_as_offline_success(
    session_factory, catalog
):
    result = await materialize(session_factory, catalog, "entry")
    async with session_factory() as session:
        file = await session.get(UploadedFile, result["file_id"])
    get_library_store().delete(file.relative_path)
    catalog.source.error = ValueError("Source offline")
    with pytest.raises(MaterializationError, match="offline"):
        await materialize(session_factory, catalog, "entry")
    with pytest.raises(CatalogEntryNotFound):
        await materialize(session_factory, catalog, "unknown")


async def test_incomplete_stream_does_not_replace_last_known_file(
    session_factory, catalog
):
    first = await materialize(session_factory, catalog, "entry")
    catalog.source.descriptor = replace(
        catalog.source.descriptor, size_bytes=len(catalog.source.data) + 1
    )
    result = await materialize(session_factory, catalog, "entry")
    assert result["file_id"] == first["file_id"] and not result["fresh"]
    assert "incomplete" in result["warning"]
    async with session_factory() as session:
        assert len(list((await session.execute(select(UploadedFile))).scalars())) == 1


async def test_concurrent_materialization_reports_busy_and_reuses_completed_revision(
    session_factory, catalog
):
    from app.services.library_catalog import CatalogSyncBusy

    catalog.source.entered, catalog.source.hold = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(materialize(session_factory, catalog, "entry"))
    await catalog.source.entered.wait()
    try:
        with pytest.raises(CatalogSyncBusy):
            await materialize(session_factory, catalog, "entry")
    finally:
        catalog.source.hold.set()
        first = await task
    assert await materialize(session_factory, catalog, "entry") == first


async def test_external_local_edit_is_not_reused_as_a_fresh_source_download(
    session_factory, catalog
):
    first = await materialize(session_factory, catalog, "entry")
    async with session_factory() as session:
        old = await session.get(UploadedFile, first["file_id"])
    store = get_library_store()
    with store.writer(old.relative_path, overwrite=True) as writer:
        writer.write(b"locally edited bytes")
    second = await materialize(session_factory, catalog, "entry")
    assert second["fresh"] and second["file_id"] != first["file_id"]
    with store.open(old.relative_path) as content:
        assert content.read() == b"locally edited bytes"
    async with session_factory() as session:
        new = await session.get(UploadedFile, second["file_id"])
    with store.open(new.relative_path) as content:
        assert content.read() == catalog.source.data
