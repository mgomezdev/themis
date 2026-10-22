"""Fleet backup carries each printer's plugin identity and import restores it (BIZ-251 review)."""
import io
import json

import pytest_asyncio
from sqlalchemy import text

from app.plugins.host import plugin_host
from tests.catalog_helpers import patch_cached_catalog

CATALOG = {"machine": [], "process": [], "filament": []}


@pytest_asyncio.fixture(autouse=True)
async def _bambu_enabled(session_factory):
    await plugin_host.start()
    await plugin_host.update_config("bambu", enabled=True)


async def _import(client, *printers):
    body = json.dumps({"themis_backup_version": 1, "printers": list(printers)}).encode()
    with patch_cached_catalog(CATALOG):
        return await client.post("/api/v1/settings/fleet-import",
                                 files={"file": ("b.json", io.BytesIO(body), "application/json")})


async def _identities(session_factory) -> list[tuple]:
    async with session_factory() as s:
        rows = await s.execute(text("SELECT name, plugin_id, manufacturer_id, model_id FROM printers ORDER BY id"))
        return [tuple(r) for r in rows]


async def test_backup_entries_carry_the_identity_triple(client):
    await client.post("/api/v1/printers", json={"name": "X", "printer_type": "bambu", "connection_config": {}})

    (entry,) = (await client.get("/api/v1/settings/fleet-backup")).json()["printers"]

    assert (entry["plugin_id"], entry["manufacturer_id"], entry["model_id"]) == ("bambu", "bambu", "p1s")


async def test_roundtrip_restores_the_identity_on_the_new_row(client, session_factory):
    created = (await client.post("/api/v1/printers", json={
        "name": "Forge", "plugin_id": "bambu", "manufacturer_id": "bambu", "model_id": "p1s", "connection_config": {}})).json()
    backup = (await client.get("/api/v1/settings/fleet-backup")).json()
    await client.delete(f"/api/v1/printers/{created['id']}")
    assert await _identities(session_factory) == []

    resp = await _import(client, *backup["printers"])

    assert resp.json()["imported"] == 1
    assert await _identities(session_factory) == [("Forge", "bambu", "bambu", "p1s")]


async def test_legacy_entry_with_only_printer_type_gets_the_legacy_mapping_identity(client, session_factory):
    resp = await _import(client, {"name": "Old", "printer_type": "bambu"})

    assert resp.json()["imported"] == 1
    assert await _identities(session_factory) == [("Old", "bambu", "bambu", "p1s")]


async def test_unregistered_plugin_identity_is_kept_verbatim_and_shown_dormant(client, session_factory):
    resp = await _import(client, {"name": "Orphan", "printer_type": "bambu",
                                  "plugin_id": "gone_plugin", "manufacturer_id": "gone_mfr", "model_id": "gone_model"})

    assert resp.json()["imported"] == 1
    assert await _identities(session_factory) == [("Orphan", "gone_plugin", "gone_mfr", "gone_model")]
    (item,) = (await client.get("/api/v1/fleet")).json()
    assert item["dormant"] is True and item["dormant_reason"] == "plugin_removed"


async def test_imported_printer_with_an_enabled_plugin_is_not_dormant(client):
    await _import(client, {"name": "Fine", "printer_type": "bambu"})

    (item,) = (await client.get("/api/v1/fleet")).json()

    assert not item.get("dormant")
