"""Catalog metadata is idempotent; only completed authoritative sweeps mark missing."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import LibraryCatalogEntry, LibraryCatalogSyncLease
from app.plugins.capabilities.library_source import CatalogPage, SourceFile
from app.services.library_catalog import CatalogSyncError, _lease, _renew, sync_catalog


def entry(key, name=None):
    return SourceFile(key, name or key, f"https://source.test/models/{key}")


class Source:
    def __init__(self, pages):
        self.pages = pages

    async def list_catalog(self, cursor=None):
        result = self.pages[cursor]
        if isinstance(result, Exception):
            raise result
        return result


class Host:
    def __init__(self, pages):
        self.source = Source(pages)

    async def call_for(self, handle, provider, fn, **kwargs):
        try:
            return SimpleNamespace(ok=True, value=await fn(self.source))
        except ValueError:
            return SimpleNamespace(ok=False, error="Source unavailable")


async def rows(factory):
    async with factory() as s:
        return {
            r.source_file_id: r
            for r in (await s.execute(select(LibraryCatalogEntry))).scalars()
        }


async def test_partial_and_incremental_sync_never_delete_previous_catalog_and_full_sweep_marks_missing(
    session_factory,
):
    host = Host({None: CatalogPage((entry("a"), entry("b")), full_scan=True)})
    assert await sync_catalog(session_factory, host, "source") == {
        "entries": 2,
        "full_scan": True,
    }
    before = await rows(session_factory)
    host.source.pages = {
        None: CatalogPage(
            (entry("a", "Updated A"),), next_cursor="page2", full_scan=True
        ),
        "page2": ValueError("network failed"),
    }
    with pytest.raises(CatalogSyncError, match="unavailable"):
        await sync_catalog(session_factory, host, "source")
    partial = await rows(session_factory)
    assert partial["a"].descriptor["name"] == "Updated A"
    assert not partial["b"].missing
    host.source.pages = {None: CatalogPage((entry("a"),), full_scan=True)}
    await sync_catalog(session_factory, host, "source")
    assert (await rows(session_factory))["b"].missing
    host.source.pages = {None: CatalogPage((entry("b"),), full_scan=False)}
    await sync_catalog(session_factory, host, "source")
    after = await rows(session_factory)
    assert not after["a"].missing and not after["b"].missing
    assert {key: row.id for key, row in after.items()} == {
        key: row.id for key, row in before.items()
    }


async def test_duplicate_pages_and_repeated_sweeps_reuse_one_row_and_cycles_do_not_mark_missing(
    session_factory,
):
    host = Host(
        {
            None: CatalogPage((entry("a"),), "next", True),
            "next": CatalogPage((entry("a"),), full_scan=True),
        }
    )
    await sync_catalog(session_factory, host, "source")
    await sync_catalog(session_factory, host, "source")
    assert len(await rows(session_factory)) == 1
    host.source.pages["next"] = CatalogPage((), "next", True)
    with pytest.raises(CatalogSyncError, match="repeated"):
        await sync_catalog(session_factory, host, "source")
    assert not (await rows(session_factory))["a"].missing


async def test_database_lease_blocks_another_process_and_cleanup_releases_it(
    session_factory,
):
    async with _lease(session_factory, "source", "first"):
        with pytest.raises(CatalogSyncError, match="already running"):
            async with _lease(session_factory, "source", "second"):
                pass
    async with _lease(session_factory, "source", "second"):
        async with session_factory() as s:
            assert (await s.get(LibraryCatalogSyncLease, "source")).owner == "second"
    async with session_factory() as s:
        assert await s.get(LibraryCatalogSyncLease, "source") is None


async def test_expired_owner_cannot_renew_or_finish_a_new_owners_sweep(session_factory):
    async with _lease(session_factory, "source", "first"):
        async with session_factory() as s:
            lease = await s.get(LibraryCatalogSyncLease, "source")
            lease.expires_at = 0
            await s.commit()
        async with _lease(session_factory, "source", "second"):
            async with session_factory() as s:
                with pytest.raises(CatalogSyncError, match="expired"):
                    await _renew(s, "source", "first")


async def test_canceled_sync_keeps_previous_metadata_and_releases_lease(
    session_factory,
):
    await sync_catalog(
        session_factory,
        Host({None: CatalogPage((entry("a"),), full_scan=True)}),
        "source",
    )
    entered = asyncio.Event()

    class HangingHost:
        async def call_for(self, *args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(sync_catalog(session_factory, HangingHost(), "source"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (await rows(session_factory))["a"].missing
    async with session_factory() as s:
        assert await s.get(LibraryCatalogSyncLease, "source") is None


async def test_source_url_dedupe_never_rebinds_another_providers_entry(session_factory):
    host = Host({None: CatalogPage((entry("a"),), full_scan=True)})
    await sync_catalog(session_factory, host, "first")
    await sync_catalog(session_factory, host, "second")
    records = await rows(session_factory)
    assert len(records) == 1 and records["a"].provider_plugin_id == "first"


@pytest.mark.parametrize(
    "bad",
    [
        SourceFile("bad", "Bad", "javascript:alert(1)"),
        SourceFile("bad", "Bad", "https://source.test/file?token=private"),
        SourceFile("bad", "Bad", "https://source.test/file", size_bytes=-1),
    ],
)
async def test_invalid_source_metadata_does_not_mark_previous_entries_missing(
    session_factory, bad
):
    await sync_catalog(
        session_factory,
        Host({None: CatalogPage((entry("a"),), full_scan=True)}),
        "source",
    )
    with pytest.raises(CatalogSyncError):
        await sync_catalog(
            session_factory, Host({None: CatalogPage((bad,), full_scan=True)}), "source"
        )
    assert not (await rows(session_factory))["a"].missing


async def test_offset_pagination_does_not_mark_a_skipped_but_existing_file_missing(
    session_factory,
):
    await sync_catalog(
        session_factory,
        Host({None: CatalogPage((entry("a"), entry("b")), full_scan=True)}),
        "source",
    )

    class ShiftingHost:
        async def call_for(self, handle, provider, fn, **kwargs):
            class Part:
                async def list_catalog(self, cursor=None):
                    return CatalogPage(
                        (entry("a"),), full_scan=True, confirm_missing=True
                    )

                async def describe(self, source_id):
                    return entry(source_id)

            return SimpleNamespace(ok=True, value=await fn(Part()))

    result = await sync_catalog(session_factory, ShiftingHost(), "source")
    assert result["entries"] == 2 and not (await rows(session_factory))["b"].missing


async def test_only_explicit_missing_identity_confirms_absence_and_outages_preserve_it(
    session_factory,
):
    from app.plugins.capabilities.library_source import SourceFileMissing

    await sync_catalog(
        session_factory,
        Host({None: CatalogPage((entry("a"), entry("b")), full_scan=True)}),
        "source",
    )

    class ConfirmingHost:
        failure = ValueError("outage")

        async def call_for(self, handle, provider, fn, **kwargs):
            failure = self.failure

            class Part:
                async def list_catalog(self, cursor=None):
                    return CatalogPage(
                        (entry("a"),), full_scan=True, confirm_missing=True
                    )

                async def describe(self, source_id):
                    raise failure

            try:
                return SimpleNamespace(ok=True, value=await fn(Part()))
            except ValueError as exc:
                return SimpleNamespace(
                    ok=False, error="Source read failed", exception=exc
                )

    host = ConfirmingHost()
    with pytest.raises(CatalogSyncError):
        await sync_catalog(session_factory, host, "source")
    assert not (await rows(session_factory))["b"].missing
    host.failure = SourceFileMissing("not accessible")
    await sync_catalog(session_factory, host, "source")
    assert (await rows(session_factory))["b"].missing


async def test_a_slow_page_renews_its_lease_without_a_network_transaction(
    session_factory, monkeypatch
):
    from app.services import library_catalog as module

    heartbeat = asyncio.Event()
    reading = False
    original = module._renew

    async def renew(session, provider, owner):
        await original(session, provider, owner)
        if reading:
            heartbeat.set()

    monkeypatch.setattr(module, "_renew", renew)
    monkeypatch.setattr(module, "LEASE_SECONDS", 0.3)

    class SlowHost:
        async def call_for(self, *args, **kwargs):
            nonlocal reading
            reading = True
            await heartbeat.wait()
            return SimpleNamespace(ok=True, value=CatalogPage((), full_scan=True))

    result = await asyncio.wait_for(
        sync_catalog(session_factory, SlowHost(), "source"), timeout=5
    )
    assert result["full_scan"] and heartbeat.is_set()
