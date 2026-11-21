from contextlib import asynccontextmanager

from app import plugins
from app.plugins.capabilities.library_source import CAPABILITY, CatalogPage, SourceFile
from app.plugins.manifest import HOST_API, PluginManifest, Provide
from tests.plugins.dummy_plugin import DummySettings


class Source:
    def __init__(self, settings):
        pass

    async def test_connection(self):
        return {"ok": True}

    async def list_catalog(self, cursor=None, changed_since=None):
        return CatalogPage(
            (SourceFile("one", "Catalog part", "https://source.test/one"),),
            full_scan=True,
        )

    async def describe(self, source_file_id):
        return SourceFile("one", "Catalog part", "https://source.test/one")

    @asynccontextmanager
    async def download(self, source_file_id, revision=None):
        yield None


async def test_catalog_sync_list_detail_and_disabled_source_keep_metadata(client):
    plugins.register_plugin(
        PluginManifest(
            id="catalog_fixture",
            name="Fixture catalog",
            version="1.0.0",
            host_api=HOST_API,
            settings_model=DummySettings,
            factory=Source,
            provides={CAPABILITY: Provide()},
        )
    )
    assert (
        await client.put("/api/v1/plugins/catalog_fixture", json={"enabled": True})
    ).status_code == 200
    sources = (await client.get("/api/v1/library/sources")).json()["sources"]
    assert next(s for s in sources if s["plugin_id"] == "catalog_fixture")["active"]
    synced = await client.post("/api/v1/library/sources/catalog_fixture/sync")
    assert synced.status_code == 200, synced.text
    assert synced.json() == {"entries": 1, "full_scan": True}
    body = (await client.get("/api/v1/library/entries")).json()
    assert body["total"] == 1 and body["items"][0]["name"] == "Catalog part"
    from tests.api.test_response_contracts import assert_carries

    assert_carries("catalog_entry", body["items"][0])
    assert_carries(
        "library_source",
        next(s for s in sources if s["plugin_id"] == "catalog_fixture"),
    )
    eid = body["items"][0]["id"]
    assert (await client.get(f"/api/v1/library/entries/{eid}")).json()[
        "cached_file_id"
    ] is None
    assert (await client.get("/api/v1/library/entries?provider=another")).json()[
        "total"
    ] == 0
    assert (await client.get("/api/v1/library/entries?limit=0")).status_code == 422
    await client.put("/api/v1/plugins/catalog_fixture", json={"enabled": False})
    assert (
        await client.post("/api/v1/library/sources/catalog_fixture/sync")
    ).status_code == 409
    assert (await client.get(f"/api/v1/library/entries/{eid}")).status_code == 200
    assert (await client.get("/api/v1/library/entries/not-an-entry")).status_code == 404


async def test_catalog_read_and_sync_have_separate_file_scopes(client, session_factory):
    from tests.api.test_inventory_api import _client_with

    reader = await _client_with(session_factory, ["files:read"])
    unrelated = await _client_with(session_factory, ["inventory:read"])
    async with reader, unrelated:
        assert (
            await reader.post("/api/v1/library/entries/absent/materialize")
        ).status_code == 403
        assert (await reader.get("/api/v1/library/sources")).status_code == 200
        assert (await reader.get("/api/v1/library/entries")).status_code == 200
        assert (
            await reader.post("/api/v1/library/sources/none/sync")
        ).status_code == 403
        assert (await unrelated.get("/api/v1/library/entries")).status_code == 403
        assert (
            await unrelated.get("/api/v1/library/files/1/origin")
        ).status_code == 403


async def test_source_errors_are_redacted_in_response_and_persisted_catalog_state(
    client,
):
    class Failing(Source):
        def __init__(self, settings):
            self.secret = settings.token

        async def list_catalog(self, cursor=None, changed_since=None):
            raise ValueError(f"upstream rejected {self.secret}")

    plugins.register_plugin(
        PluginManifest(
            id="failed_catalog",
            name="Failed catalog",
            version="1.0.0",
            host_api=HOST_API,
            settings_model=DummySettings,
            factory=Failing,
            provides={CAPABILITY: Provide()},
            secret_fields=frozenset({"token"}),
        )
    )
    await client.put(
        "/api/v1/plugins/failed_catalog",
        json={"enabled": True, "secrets": {"token": "fixture-private-key"}},
    )
    response = await client.post("/api/v1/library/sources/failed_catalog/sync")
    assert response.status_code == 502 and "fixture-private-key" not in response.text
    assert "***" in response.text
    listing = await client.get("/api/v1/library/sources")
    assert "fixture-private-key" not in listing.text


async def test_materialization_routes_real_local_file_and_reports_disabled_source_cache(
    client, tmp_path, monkeypatch
):
    from app import config
    from app.plugins.capabilities.library_source import SourceDownload

    monkeypatch.setattr(config, "get_library_dir", lambda: tmp_path / "library")
    monkeypatch.setattr(config, "get_filecache_dir", lambda: tmp_path / "filecache")

    class DownloadSource(Source):
        async def list_catalog(self, cursor=None, changed_since=None):
            return CatalogPage((await self.describe("one"),), full_scan=True)

        async def describe(self, source_file_id):
            return SourceFile("one", "part.stl", "https://source.test/one")

        @asynccontextmanager
        async def download(self, source_file_id, revision=None):
            async def chunks():
                yield b"solid part\nendsolid part\n"

            yield SourceDownload(chunks())

    plugins.register_plugin(
        PluginManifest(
            id="downloads",
            name="Downloads",
            version="1.0.0",
            host_api=HOST_API,
            settings_model=DummySettings,
            factory=DownloadSource,
            provides={CAPABILITY: Provide()},
        )
    )
    await client.put("/api/v1/plugins/downloads", json={"enabled": True})
    await client.post("/api/v1/library/sources/downloads/sync")
    entry = (await client.get("/api/v1/library/entries")).json()["items"][0]
    result = await client.post(f"/api/v1/library/entries/{entry['id']}/materialize")
    assert result.status_code == 200, result.text
    result = result.json()
    from tests.api.test_response_contracts import assert_carries

    assert_carries("library_materialization", result)
    assert result["fresh"] and result["warning"] is None
    downloaded = await client.get(f"/api/v1/files/{result['file_id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == b"solid part\nendsolid part\n"
    origin = (
        await client.get(f"/api/v1/library/files/{result['file_id']}/origin")
    ).json()["origin"]
    assert_carries("file_source_origin", origin)
    assert origin["source_url"] == "https://source.test/one" and origin["is_latest"]
    await client.put("/api/v1/plugins/downloads", json={"enabled": False})
    assert (
        await client.get(f"/api/v1/library/files/{result['file_id']}/origin")
    ).json()["origin"] == origin
    cached = await client.post(f"/api/v1/library/entries/{entry['id']}/materialize")
    assert cached.status_code == 200 and not cached.json()["fresh"]
    assert (
        cached.json()["file_id"] == result["file_id"]
        and "last-known" in cached.json()["warning"]
    )
    assert (
        await client.post("/api/v1/library/entries/absent/materialize")
    ).status_code == 404


async def test_local_files_have_no_invented_source_and_missing_file_is_404(
    client, session_factory
):
    from app.models import UploadedFile

    async with session_factory() as session:
        file = UploadedFile(
            original_filename="local.stl", relative_path="local.stl", uploaded_at="now"
        )
        session.add(file)
        await session.commit()
        file_id = file.id
    assert (await client.get(f"/api/v1/library/files/{file_id}/origin")).json() == {
        "origin": None
    }
    assert (await client.get("/api/v1/library/files/99999/origin")).status_code == 404


async def test_busy_operations_are_conflicts_and_invalid_streams_are_upstream_errors(
    client, session_factory
):
    from app.services.library_catalog import _lease

    class InvalidDownload(Source):
        async def list_catalog(self, cursor=None, changed_since=None):
            return CatalogPage((await self.describe("one"),), full_scan=True)

        async def describe(self, source_file_id):
            return SourceFile("one", "part.stl", "https://source.test/one")

    plugins.register_plugin(
        PluginManifest(
            id="busy_source",
            name="Busy source",
            version="1.0.0",
            host_api=HOST_API,
            settings_model=DummySettings,
            factory=InvalidDownload,
            provides={CAPABILITY: Provide()},
        )
    )
    await client.put("/api/v1/plugins/busy_source", json={"enabled": True})
    assert (
        await client.post("/api/v1/library/sources/busy_source/sync")
    ).status_code == 200
    entry = (await client.get("/api/v1/library/entries")).json()["items"][0]
    async with _lease(session_factory, "busy_source", "another-process"):
        assert (
            await client.post("/api/v1/library/sources/busy_source/sync")
        ).status_code == 409
        assert (
            await client.post(f"/api/v1/library/entries/{entry['id']}/materialize")
        ).status_code == 409
    failed = await client.post(f"/api/v1/library/entries/{entry['id']}/materialize")
    assert (
        failed.status_code == 502
        and "invalid download stream" in failed.json()["detail"]
    )
    assert (await client.get(f"/api/v1/library/entries/{entry['id']}")).json()[
        "cached_file_id"
    ] is None
