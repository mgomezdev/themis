"""Routed sources coexist; source IDs never fall back to another provider."""
import asyncio
from contextlib import asynccontextmanager
from app import plugins
from app.plugins.capabilities.library_source import CAPABILITY, LIBRARY_SOURCE, CatalogPage, SourceDownload, SourceFile
from app.plugins.manifest import HOST_API, PluginManifest, Provide
from tests.plugins.dummy_plugin import DummySettings


class Source:
    def __init__(self, settings):
        self.label, self.closed = settings.url, False

    async def test_connection(self): return {"ok": True}

    async def list_catalog(self, cursor=None, changed_since=None):
        return CatalogPage((await self.describe("opaque-id"),), full_scan=True)

    async def describe(self, source_file_id):
        return SourceFile(source_file_id, self.label, f"https://fixture.invalid/{source_file_id}", revision="v1")

    @asynccontextmanager
    async def download(self, source_file_id, revision=None):
        async def chunks(): yield b"model bytes"
        try:
            yield SourceDownload(chunks(), revision="v1")
        finally:
            self.closed = True


def register(pid, factory=Source):
    plugins.register_plugin(PluginManifest(id=pid, name=pid, version="1.0.0", host_api=HOST_API,
        settings_model=DummySettings, factory=factory, provides={CAPABILITY: Provide()}))


async def test_two_sources_coexist_without_selection_and_disabled_entries_never_fall_back(host):
    register("source_a"); register("source_b")
    await host.start()
    for pid in ("source_a", "source_b"):
        await host.update_config(pid, enabled=True, settings={"url": pid})
    assert host.selected(CAPABILITY) is None
    assert set(host.active_providers(CAPABILITY)) == {"source_a", "source_b"}
    a = await host.call_for(LIBRARY_SOURCE, "source_a", lambda p: p.list_catalog(changed_since="yesterday"))
    b = await host.call_for(LIBRARY_SOURCE, "source_b", lambda p: p.describe("opaque-id"))
    assert a.ok and a.value.entries[0].name == "source_a" and a.value.full_scan
    assert b.ok and b.value.name == "source_b"
    await host.update_config("source_a", enabled=False)
    absent = await host.call_for(LIBRARY_SOURCE, "source_a", lambda p: p.describe("opaque-id"))
    assert not absent.ok and absent.reason == "inactive"
    assert (await host.call_for(LIBRARY_SOURCE, "source_b", lambda p: p.describe("opaque-id"))).ok


async def test_missing_and_incomplete_sources_return_inactive(host):
    class Incomplete:
        def __init__(self, settings): pass
    register("broken_source", Incomplete)
    await host.start()
    await host.update_config("broken_source", enabled=True)
    for pid in ("absent_source", "broken_source"):
        result = await host.call_for(LIBRARY_SOURCE, pid, lambda p: p.describe("id"))
        assert not result.ok and result.reason == "inactive"
    assert "LibrarySource" in host.rejections("broken_source")[CAPABILITY]


async def test_stream_consumption_is_contained_and_closed_on_success_and_failure(host):
    register("source_a")
    await host.start()
    await host.update_config("source_a", enabled=True)
    provider = host.active_for(CAPABILITY, "source_a").part
    async def consume(p):
        async with p.download("opaque-id", "v1") as stream:
            return b"".join([chunk async for chunk in stream.chunks])
    result = await host.call_for(LIBRARY_SOURCE, "source_a", consume)
    assert result.ok and result.value == b"model bytes" and provider.closed
    provider.closed = False
    async def fail(p):
        async with p.download("opaque-id", "v1"):
            raise ValueError("consumer rejected file size")
    result = await host.call_for(LIBRARY_SOURCE, "source_a", fail)
    assert not result.ok and result.reason == "error" and provider.closed


async def test_a_timed_out_download_closes_its_stream_and_leaves_another_source_serving(host):
    register("source_a"); register("source_b")
    await host.start()
    for pid in ("source_a", "source_b"):
        await host.update_config(pid, enabled=True)
    provider = host.active_for(CAPABILITY, "source_a").part
    async def wait_for_bytes(p):
        async with p.download("opaque-id", "v1"):
            await asyncio.Event().wait()
    result = await host.call_for(LIBRARY_SOURCE, "source_a", wait_for_bytes, timeout=0.05)
    assert not result.ok and result.reason == "timeout" and provider.closed
    assert (await host.call_for(LIBRARY_SOURCE, "source_b", lambda p: p.describe("opaque-id"))).ok
