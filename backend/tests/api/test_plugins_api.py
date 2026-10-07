"""`/api/v1/plugins` + `/api/v1/extension-slots/{kind}` (BIZ-215) and the legacy Spoolman alias interplay."""
import httpx
import pytest

from app.plugins.capabilities.filament_inventory import REMOTE, TRACKS_WEIGHT
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import enable_spoolman, use_provider

KEY = "hunter2-api-key"


async def test_list_plugins_describes_manifests_ui_capabilities_and_state(client):
    body = (await client.get("/api/v1/plugins")).json()

    assert body["slots"] == {"filament_inventory": None}
    (sm,) = [p for p in body["plugins"] if p["id"] == "spoolman"]
    assert (sm["name"], sm["kind"], sm["source"], sm["enabled"], sm["active"], sm["error"]) == (
        "Spoolman", "filament_inventory", "bundled", False, False, None)
    assert {"TRACKS_WEIGHT", "WRITE_WEIGHT", "REMOTE", "LABEL_SCAN"} <= set(sm["capabilities"])
    assert sm["ui"]["mode"] == "page" and [t["id"] for t in sm["ui"]["tabs"]] == ["connection", "mappings"]
    assert sm["ui"]["tabs"][1]["renderer"] == "component"


async def test_plugin_detail_has_settings_secret_flags_and_schema_but_never_a_secret(client):
    await client.put("/api/v1/plugins/spoolman", json={"settings": {"url": "http://sm.test"}, "secrets": {"api_key": KEY}})

    resp = await client.get("/api/v1/plugins/spoolman")
    body = resp.json()

    assert body["settings"]["url"] == "http://sm.test" and body["settings"]["sync_interval_minutes"] == 15
    assert "api_key" not in body["settings"] and body["secrets"] == {"api_key": True} and body["secret_fields"] == ["api_key"]
    assert "url" in body["settings_schema"]["properties"]
    assert KEY not in resp.text and KEY not in (await client.get("/api/v1/plugins")).text


async def test_updating_a_plugin_validates_and_never_echoes_a_secret(client):
    assert (await client.get("/api/v1/plugins/nope_nope")).status_code == 404
    assert (await client.put("/api/v1/plugins/nope_nope", json={"enabled": True})).status_code == 404

    bad = await client.put("/api/v1/plugins/spoolman", json={"settings": {"sync_interval_minutes": 0}, "secrets": {"api_key": KEY}})
    assert bad.status_code == 422 and "sync_interval_minutes" in bad.json()["detail"] and KEY not in bad.text
    leaked = await client.put("/api/v1/plugins/spoolman", json={"settings": {"api_key": KEY}})
    assert leaked.status_code == 422 and KEY not in leaked.text
    notsecret = await client.put("/api/v1/plugins/spoolman", json={"secrets": {"url": "x"}})
    assert notsecret.status_code == 422
    assert (await client.get("/api/v1/plugins/spoolman")).json()["secrets"] == {"api_key": False}     # nothing was saved


async def test_selecting_a_provider_activates_it_and_it_can_be_disabled_and_cleared(client):
    resp = await client.put("/api/v1/extension-slots/filament_inventory", json={"plugin_id": "spoolman"})
    assert resp.json() == {"kind": "filament_inventory", "plugin_id": "spoolman"}
    listed = (await client.get("/api/v1/plugins")).json()
    assert listed["slots"]["filament_inventory"] == "spoolman"
    sm = next(p for p in listed["plugins"] if p["id"] == "spoolman")
    assert sm["enabled"] is True and sm["active"] is False and sm["error"]       # selected + enabled, but no URL yet: reported

    await client.put("/api/v1/plugins/spoolman", json={"settings": {"url": "http://sm.test"}})
    assert next(p for p in (await client.get("/api/v1/plugins")).json()["plugins"] if p["id"] == "spoolman")["active"] is True

    await client.put("/api/v1/plugins/spoolman", json={"enabled": False})
    after = (await client.get("/api/v1/plugins")).json()
    assert after["slots"]["filament_inventory"] == "spoolman"                      # the choice survives
    assert next(p for p in after["plugins"] if p["id"] == "spoolman")["active"] is False

    assert (await client.put("/api/v1/extension-slots/filament_inventory", json={"plugin_id": None})).json()["plugin_id"] is None


async def test_a_slot_only_accepts_a_plugin_of_its_own_kind(client):
    assert (await client.put("/api/v1/extension-slots/filament_inventory", json={"plugin_id": "nope_nope"})).status_code == 422
    assert (await client.put("/api/v1/extension-slots/printer_vendor", json={"plugin_id": "spoolman"})).status_code == 422


async def test_test_connection_uses_unsaved_settings_and_saved_secrets_without_echoing_them(client, spoolman_upstream):
    await client.put("/api/v1/plugins/spoolman", json={"secrets": {"api_key": KEY}})

    ok = await client.post("/api/v1/plugins/spoolman/test", json={"settings": {"url": "http://candidate.test"}})
    assert ok.json() == {"ok": True, "version": "1.0.0-mock"}
    assert [(str(r.url), r.headers.get("X-API-Key")) for r in spoolman_upstream.requests] == [("http://candidate.test/api/v1/info", KEY)]

    spoolman_upstream.handler = lambda request: httpx.Response(500, text=f"upstream said {KEY}")
    failed = await client.post("/api/v1/plugins/spoolman/test", json={"settings": {"url": "http://candidate.test"}})
    assert failed.json()["ok"] is False and KEY not in failed.text
    nourl = await client.post("/api/v1/plugins/spoolman/test", json={})
    assert nourl.json() == {"ok": False, "message": "Spoolman URL is not set"}        # the provider refuses to build without one


async def test_the_plugin_routes_need_the_settings_scopes(client, session_factory):
    from tests.api.test_inventory_api import _client_with
    reader = await _client_with(session_factory, ["settings:read"])
    inventory_only = await _client_with(session_factory, ["inventory:read", "inventory:write"])
    async with reader, inventory_only:
        assert (await reader.get("/api/v1/plugins")).status_code == 200
        assert (await reader.put("/api/v1/plugins/spoolman", json={"enabled": True})).status_code == 403
        assert (await reader.put("/api/v1/extension-slots/filament_inventory", json={"plugin_id": None})).status_code == 403
        assert (await inventory_only.get("/api/v1/plugins")).status_code == 403


# --- the deprecated Spoolman aliases follow the host --------------------------------------------------------------------------

async def test_the_legacy_settings_put_selects_spoolman_as_the_provider_and_disabling_keeps_the_choice(client):
    await client.put("/api/v1/settings/spoolman", json={"enabled": True, "url": "http://sm.test", "api_key": KEY})
    assert (await client.get("/api/v1/plugins")).json()["slots"]["filament_inventory"] == "spoolman"
    assert (await client.get("/api/v1/inventory/sync-status")).json()["provider"] == "spoolman"

    off = await client.put("/api/v1/settings/spoolman", json={"enabled": False})
    assert off.json() == {"enabled": False, "url": "http://sm.test", "has_api_key": True, "sync_interval_minutes": 15}
    assert (await client.get("/api/v1/inventory/sync-status")).json()["provider"] is None
    assert (await client.get("/api/v1/plugins")).json()["slots"]["filament_inventory"] == "spoolman"


async def test_legacy_routes_answer_409_when_another_provider_is_active(client):
    await use_provider(FakeInventoryProvider(capabilities=frozenset({REMOTE, TRACKS_WEIGHT})), plugin_id="other_inventory")
    for method, path, body in [("GET", "/api/v1/spoolman/filaments", None), ("GET", "/api/v1/spoolman/spools", None),
                               ("POST", "/api/v1/spoolman/sync-now", None),
                               ("PATCH", "/api/v1/spoolman/filaments/1", {"orca_profiles": {}})]:
        resp = await client.request(method, path, json=body)
        assert resp.status_code == 409 and "api/v1/inventory" in resp.json()["detail"], (method, path)
    assert (await client.get("/api/v1/spoolman/sync-status")).status_code == 200            # status never errors


async def test_enabling_spoolman_via_the_legacy_put_replaces_another_active_provider(client):
    await use_provider(FakeInventoryProvider(), plugin_id="other_inventory")
    await client.put("/api/v1/settings/spoolman", json={"enabled": True, "url": "http://sm.test"})
    assert (await client.get("/api/v1/plugins")).json()["slots"]["filament_inventory"] == "spoolman"


async def test_a_candidate_secret_is_masked_in_a_failed_connection_test(client, spoolman_upstream):
    candidate = "candidate-key-123"
    spoolman_upstream.handler = lambda request: httpx.Response(401, text=f"bad key {candidate}")

    new_route = await client.post("/api/v1/plugins/spoolman/test", json={"settings": {"url": "http://c.test"}, "secrets": {"api_key": candidate}})
    legacy = await client.post("/api/v1/settings/spoolman/test", json={"url": "http://c.test", "api_key": candidate})

    for resp in (new_route, legacy):
        assert resp.json()["ok"] is False and candidate not in resp.text


# ---- schema tabs (the generic renderer's data source) -------------------------------------------------------------------

def _schema_plugin(ui_schema):
    from pydantic import BaseModel
    from app import plugins
    from app.plugins.manifest import HOST_API, PluginManifest, UiContribution, UiTab

    class _S(BaseModel):
        pass

    manifest = PluginManifest(
        id="schema_demo", name="Schema demo", kind="filament_inventory", version="1", host_api=HOST_API, settings_model=_S,
        factory=lambda _s: FakeInventoryProvider(), ui=UiContribution(mode="page", tabs=(
            UiTab("library", "Library", "schema"), UiTab("conn", "Connection", "default"))), ui_schema=ui_schema)
    plugins.register_plugin(manifest)
    return manifest


async def test_a_schema_tab_serves_the_schema_its_plugin_provides(client):
    doc = {"title": "Library", "blocks": [{"type": "table", "data": "materials", "columns": [{"key": "name", "label": "Name"}]}]}
    _schema_plugin(lambda tab: doc if tab == "library" else None)

    resp = await client.get("/api/v1/plugins/schema_demo/ui/library")

    assert resp.status_code == 200 and resp.json() == doc
    (demo,) = [p for p in (await client.get("/api/v1/plugins")).json()["plugins"] if p["id"] == "schema_demo"]
    assert [t["renderer"] for t in demo["ui"]["tabs"]] == ["schema", "default"]


@pytest.mark.parametrize("path", ["/api/v1/plugins/schema_demo/ui/conn",          # a default tab has no schema
                                  "/api/v1/plugins/schema_demo/ui/missing",         # not a tab at all
                                  "/api/v1/plugins/spoolman/ui/mappings",           # a component tab
                                  "/api/v1/plugins/nope_nope/ui/library"])           # unknown plugin
async def test_only_schema_tabs_have_a_schema(client, path):
    _schema_plugin(lambda tab: {"blocks": []})
    assert (await client.get(path)).status_code == 404


def test_a_schema_tab_without_a_schema_provider_is_rejected_at_registration():
    from app.plugins.manifest import PluginError
    with pytest.raises(PluginError, match="needs a ui_schema provider"):
        _schema_plugin(None)


async def test_a_schema_tab_needs_the_settings_read_scope(client, session_factory):
    from tests.api.test_inventory_api import _client_with
    _schema_plugin(lambda tab: {"blocks": []})
    narrow = await _client_with(session_factory, ["inventory:read"])
    async with narrow:
        assert (await narrow.get("/api/v1/plugins/schema_demo/ui/library")).status_code == 403
