"""PATCH /printers/{id}, POST /printers/{id}/reconnect and the OrcaSlicer machine-catalog routes."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.services.catalog_service import CatalogUnavailable
from tests.catalog_helpers import patch_cached_catalog
from fastapi import HTTPException

from app.services.printer_manager import printer_manager

_PRINTER_KEYS = {"id", "name", "printer_type", "plugin_id", "manufacturer_id", "model_id", "connection_config", "awaiting_plate_clear", "orca_printer_profiles",
                 "current_orca_printer_profile", "enabled", "queue_on", "loaded_filaments", "build_plate_type",
                 "no_snapshots_while_idle", "bed_x_mm", "bed_y_mm", "machine_rate_per_hour", "quiet_start", "quiet_end", "connected"}  # ApiPrinter in src/api/printers.ts


@pytest.fixture
async def printer(client) -> dict:
    resp = await client.post("/api/v1/printers", json={
        "name": "Forge", "printer_type": "elegoo_centauri", "connection_config": {"ip_address": "10.0.0.5"},
        "orca_printer_profiles": ["Machine A"], "current_orca_printer_profile": "Machine A",
        "loaded_filaments": [{"slot": 0, "type": "PLA"}], "build_plate_type": "Smooth PEI",
        "bed_x_mm": 220.0, "bed_y_mm": 220.0,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _get(client, printer_id: int) -> dict:
    return (await client.get(f"/api/v1/printers/{printer_id}")).json()


# ---------------------------------------------------------------------------
# PATCH /printers/{id}
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field, value", [
    ("name", "Anvil"),
    ("orca_printer_profiles", ["Machine B", "Machine C"]),
    ("current_orca_printer_profile", "Machine B"),
    ("enabled", False),
    ("queue_on", False),
    ("loaded_filaments", [{"slot": 1, "type": "PETG", "color": "#00FF00"}]),
    ("build_plate_type", "Textured PEI"),
    ("no_snapshots_while_idle", True),
    ("bed_x_mm", 350.0),
    ("bed_y_mm", 300.0),
    ("machine_rate_per_hour", 1.25),
])
async def test_patch_changes_only_the_field_it_was_given(client, printer, field, value):
    before = await _get(client, printer["id"])

    resp = await client.patch(f"/api/v1/printers/{printer['id']}", json={field: value})

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _PRINTER_KEYS
    assert body == {**before, field: value}  # nothing else moved
    assert await _get(client, printer["id"]) == body  # and it persisted


async def test_patch_explicit_null_clears_the_active_preset_and_plate_type_but_omission_keeps_them(client, printer):
    kept = await client.patch(f"/api/v1/printers/{printer['id']}", json={"name": "Renamed"})
    assert (kept.json()["current_orca_printer_profile"], kept.json()["build_plate_type"]) == ("Machine A", "Smooth PEI")

    cleared = await client.patch(f"/api/v1/printers/{printer['id']}",
                                 json={"current_orca_printer_profile": None, "build_plate_type": None})

    assert (cleared.json()["current_orca_printer_profile"], cleared.json()["build_plate_type"]) == (None, None)
    assert cleared.json()["name"] == "Renamed"


async def test_patch_replaces_connection_config_wholesale_without_touching_the_live_client(client, printer):
    """Pins current behavior: a new IP is saved but the running client keeps its old connection until the
    operator hits Reconnect (there is no automatic reconnect on edit)."""
    await client.patch(f"/api/v1/printers/{printer['id']}",
                       json={"connection_config": {"ip_address": "10.0.0.5", "stale_key": "old"}})
    live = MagicMock()
    live.connected = True
    printer_manager._clients[printer["id"]] = live

    resp = await client.patch(f"/api/v1/printers/{printer['id']}", json={"connection_config": {"ip_address": "10.0.0.99"}})

    assert resp.json()["connection_config"] == {"ip_address": "10.0.0.99"}  # replaced, not merged
    assert resp.json()["connected"] is True
    assert printer_manager._clients[printer["id"]] is live
    live.disconnect.assert_not_called()


@pytest.mark.parametrize("body", [{"bed_x_mm": "wide"}, {"enabled": "maybe"}, {"loaded_filaments": "PLA"},
                                  {"orca_printer_profiles": "Machine A"}])
async def test_patch_rejects_wrongly_typed_fields_and_changes_nothing(client, printer, body):
    before = await _get(client, printer["id"])

    resp = await client.patch(f"/api/v1/printers/{printer['id']}", json=body)

    assert resp.status_code == 422
    assert await _get(client, printer["id"]) == before


async def test_patch_404_for_a_missing_printer(client):
    resp = await client.patch("/api/v1/printers/999", json={"name": "x"})
    assert (resp.status_code, resp.json()["detail"]) == (404, "Printer 999 not found")


# ---------------------------------------------------------------------------
# POST /printers/{id}/reconnect
# ---------------------------------------------------------------------------

async def test_reconnect_404_for_a_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/reconnect")
    assert resp.status_code == 404


async def test_reconnect_drops_the_old_session_and_registers_a_fresh_client(client, printer):
    old = MagicMock()
    printer_manager._clients[printer["id"]] = old
    fresh = MagicMock()

    with patch("app.api.routes.printers.create_client", return_value=fresh) as build:
        resp = await client.post(f"/api/v1/printers/{printer['id']}/reconnect")

    assert (resp.status_code, resp.json()) == (200, {"ok": True})
    old.disconnect.assert_called_once()
    assert printer_manager._clients[printer["id"]] is fresh
    assert build.call_args.args[0].id == printer["id"]  # built from the stored printer row


async def test_reconnect_failure_is_503_and_leaves_the_printer_without_a_client(client, printer):
    """Pins current behavior: the old session is torn down BEFORE the new one is attempted."""
    old = MagicMock()
    printer_manager._clients[printer["id"]] = old

    with patch("app.api.routes.printers.create_client", side_effect=RuntimeError("bad access code")):
        resp = await client.post(f"/api/v1/printers/{printer['id']}/reconnect")

    assert (resp.status_code, resp.json()["detail"]) == (503, "Failed to connect: bad access code")
    old.disconnect.assert_called_once()
    assert printer["id"] not in printer_manager._clients
    assert (await _get(client, printer["id"]))["connected"] is False


# ---------------------------------------------------------------------------
# OrcaSlicer machine catalog routes
# ---------------------------------------------------------------------------

_CATALOG = {"machine": [
    {"name": "Zed X 0.4", "manufacturer": "Zed", "model": "X", "nozzle": "0.4", "uuid": "u-3"},
    {"name": "Acme P 0.6", "manufacturer": "Acme", "model": "P", "nozzle": "0.6", "uuid": "u-2"},
    {"name": "Acme P 0.4", "manufacturer": "Acme", "model": "P", "nozzle": "0.4", "uuid": "u-1"},
    {"name": "Generic 0.4", "model": "G", "nozzle": "0.4"},          # no manufacturer/uuid -> blanks
    {"name": "No Nozzle", "manufacturer": "Acme", "model": "P"},     # not selectable
    {"name": "No Model", "manufacturer": "Acme", "nozzle": "0.4"},   # not selectable
    {"manufacturer": "Acme", "model": "P", "nozzle": "0.4"},         # nameless
]}


async def _with_catalog(client, method: str, path: str, catalog):
    with patch_cached_catalog(catalog):
        return await getattr(client, method)(path)


async def test_machine_catalog_lists_selectable_presets_sorted_with_the_keys_the_frontend_reads(client):
    resp = await _with_catalog(client, "get", "/api/v1/printers/orca-machine-catalog", _CATALOG)

    assert resp.status_code == 200
    rows = resp.json()
    assert all(set(r) == {"name", "vendor", "printer_model", "nozzle", "source", "uuid"} for r in rows)
    assert [(r["vendor"], r["printer_model"], r["nozzle"], r["name"]) for r in rows] == [
        ("", "G", "0.4", "Generic 0.4"), ("Acme", "P", "0.4", "Acme P 0.4"),
        ("Acme", "P", "0.6", "Acme P 0.6"), ("Zed", "X", "0.4", "Zed X 0.4")]
    assert rows[0] == {"name": "Generic 0.4", "vendor": "", "printer_model": "G", "nozzle": "0.4",
                       "source": "system", "uuid": ""}
    assert rows[1]["uuid"] == "u-1"


async def test_machine_catalog_is_empty_when_the_catalog_is_unavailable(client):
    resp = await _with_catalog(client, "get", "/api/v1/printers/orca-machine-catalog", RuntimeError("sidecar down"))
    assert (resp.status_code, resp.json()) == (200, [])


async def test_orca_presets_are_sorted_unique_names(client):
    catalog = {"machine": [{"name": "B"}, {"name": "A"}, {"name": "B"}, {"manufacturer": "nameless"}]}

    resp = await _with_catalog(client, "get", "/api/v1/printers/orca-presets", catalog)

    assert resp.json() == ["A", "B"]
    assert (await _with_catalog(client, "get", "/api/v1/printers/orca-presets", RuntimeError("x"))).json() == []


async def test_rescan_profiles_counts_machines_that_have_a_model_and_nozzle(client):
    named = {"machine": _CATALOG["machine"][:-1]}  # real catalogs always name their presets
    with patch("app.services.catalog_service.refresh", new=AsyncMock()) as refresh:
        resp = await _with_catalog(client, "post", "/api/v1/printers/rescan-profiles", named)
        refresh.assert_awaited_once()
        assert (resp.status_code, resp.json()) == (200, {"machine_presets": 4})

        unavailable = await _with_catalog(client, "post", "/api/v1/printers/rescan-profiles", RuntimeError("x"))
        assert unavailable.json() == {"machine_presets": 0}


async def test_rescan_profiles_surfaces_a_sidecar_refresh_failure(client):
    with patch("app.services.catalog_service.refresh",
               new=AsyncMock(side_effect=CatalogUnavailable("Laminus sidecar unreachable", 502))):
        resp = await client.post("/api/v1/printers/rescan-profiles")

    assert (resp.status_code, resp.json()["detail"]) == (502, "Laminus sidecar unreachable")
