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
        assert (await reader.get("/api/v1/library/sources")).status_code == 200
        assert (await reader.get("/api/v1/library/entries")).status_code == 200
        assert (
            await reader.post("/api/v1/library/sources/none/sync")
        ).status_code == 403
        assert (await unrelated.get("/api/v1/library/entries")).status_code == 403


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
