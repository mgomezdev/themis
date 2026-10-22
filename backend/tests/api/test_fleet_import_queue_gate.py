"""An imported printer joins the dormancy gate: one whose plugin is not installed is never queue-ready (BIZ-251 review)."""
import io
import json

import pytest_asyncio
from unittest.mock import MagicMock

from app.plugins.host import plugin_host
from app.services.printer_manager import printer_manager
from tests.catalog_helpers import patch_cached_catalog


@pytest_asyncio.fixture(autouse=True)
async def _bambu_enabled(session_factory):
    await plugin_host.start()
    await plugin_host.update_config("bambu", enabled=True)


async def _import_one(client, **fields) -> int:
    body = json.dumps({"themis_backup_version": 1, "printers": [{"name": "Imported", "printer_type": "bambu", **fields}]}).encode()
    with patch_cached_catalog({"machine": [], "process": [], "filament": []}):
        resp = await client.post("/api/v1/settings/fleet-import", files={"file": ("b.json", io.BytesIO(body), "application/json")})
    assert resp.status_code == 200, resp.text
    (item,) = [i for i in (await client.get("/api/v1/fleet")).json() if i["name"] == "Imported"]
    return item["id"]


async def test_an_imported_printer_of_a_missing_plugin_is_never_ready_even_with_a_live_client(client):
    pid = await _import_one(client, plugin_id="gone_plugin", manufacturer_id="gone_mfr", model_id="gone_model")
    printer_manager.register_client(pid, MagicMock(connected=True, is_idle=True))      # would be ready if it were not dormant

    assert printer_manager.is_printer_ready(pid) is False


async def test_an_imported_printer_of_an_installed_plugin_is_ready(client):
    pid = await _import_one(client)                                                      # legacy entry -> bambu
    printer_manager.register_client(pid, MagicMock(connected=True, is_idle=True))

    assert printer_manager.is_printer_ready(pid) is True
