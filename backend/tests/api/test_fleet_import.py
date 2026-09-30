"""POST /settings/fleet-import edge cases (the backup/redaction basics live in tests/test_fleet_backup.py)."""
import io
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.printer_manager import printer_manager

CATALOG = {"machine": [{"name": "Known Machine"}], "process": [], "filament": [{"name": "Known Filament"}]}


async def _import(client, payload, *, catalog=CATALOG):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    catalog_mock = AsyncMock(side_effect=catalog) if isinstance(catalog, Exception) else AsyncMock(return_value=catalog)
    with patch("app.api.routes.laminus.get_cached_catalog", catalog_mock):
        return await client.post("/api/v1/settings/fleet-import",
                                 files={"file": ("backup.json", io.BytesIO(body), "application/json")})


def _backup(*printers, version=1) -> dict:
    return {"themis_backup_version": version, "printers": list(printers)}


def _printer(name="P", printer_type="mock", **fields) -> dict:
    return {"name": name, "printer_type": printer_type, **fields}


async def _fleet(client) -> list[dict]:
    return (await client.get("/api/v1/printers")).json()


# ---------------------------------------------------------------------------
# Rejected files change nothing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload, detail", [
    (b"", "Invalid JSON"),
    (b"not json at all", "Invalid JSON"),
    (b"[]", "Not a valid Themis fleet backup file"),
    (json.dumps({"themis_backup_version": 1}).encode(), "Not a valid Themis fleet backup file"),
    (json.dumps({"printers": []}).encode(), "Unsupported backup version: 0"),  # version defaults to 0
    (json.dumps({"printers": [], "themis_backup_version": 0}).encode(), "Unsupported backup version: 0"),
    (json.dumps({"printers": [], "themis_backup_version": -3}).encode(), "Unsupported backup version: -3"),
])
async def test_invalid_backups_are_400_and_leave_the_fleet_untouched(client, payload, detail):
    await client.post("/api/v1/printers", json={"name": "Existing", "printer_type": "mock", "connection_config": {}})
    before = await _fleet(client)

    resp = await _import(client, payload)

    assert resp.status_code == 400
    assert detail in resp.json()["detail"]
    assert await _fleet(client) == before


# ---------------------------------------------------------------------------
# Partial imports and reporting
# ---------------------------------------------------------------------------

async def test_unknown_printer_types_are_skipped_with_a_warning_while_the_rest_import(client):
    resp = await _import(client, _backup(
        _printer("Legacy Klipper", "moonraker"),
        _printer("Good One", "mock"),
        {"name": "No Type"},
    ))

    assert resp.status_code == 200
    assert resp.json() == {"imported": 1, "skipped": 2, "warnings": [
        "'Legacy Klipper': skipped — unknown printer type 'moonraker'",
        "'No Type': skipped — unknown printer type ''",
    ]}
    assert [p["name"] for p in await _fleet(client)] == ["Good One"]


async def test_unnamed_printers_get_a_placeholder_name_and_optional_fields_default(client):
    resp = await _import(client, _backup({"printer_type": "mock"}))

    assert resp.json()["imported"] == 1
    (p,) = await _fleet(client)
    assert p["name"] == "Unnamed Printer"
    assert (p["enabled"], p["queue_on"], p["loaded_filaments"]) == (True, True, [])
    assert p["orca_printer_profiles"] == [] and p["current_orca_printer_profile"] is None


async def test_duplicate_names_are_imported_as_is_and_importing_twice_doubles_the_fleet(client):
    """Pins current behavior (Printer.name is not unique and import never merges) — see the
    'decide if intended' flag in the test-coverage review."""
    backup = _backup(_printer("Twin"), _printer("Twin"))

    assert (await _import(client, backup)).json()["imported"] == 2
    assert (await _import(client, backup)).json()["imported"] == 2

    fleet = await _fleet(client)
    assert [p["name"] for p in fleet] == ["Twin"] * 4
    assert len({p["id"] for p in fleet}) == 4


async def test_unknown_orca_profiles_warn_and_known_ones_do_not(client):
    resp = await _import(client, _backup(_printer(
        "Warned",
        orca_printer_profiles=["Known Machine", "Ghost Machine"],
        current_orca_printer_profile="Ghost Active",
        loaded_filaments=[{"slot": 0, "filament_profile": "Known Filament"},
                          {"slot": 2, "filament_profile": "Ghost Filament"},
                          {"slot": 3}],
    )))

    assert resp.status_code == 200
    assert resp.json()["warnings"] == [
        "'Warned': Orca machine profile 'Ghost Machine' not found in catalog",
        "'Warned': active Orca profile 'Ghost Active' not found in catalog",
        "'Warned' slot 2: filament profile 'Ghost Filament' not found in catalog",
    ]
    (p,) = await _fleet(client)  # warnings never block the import, and names are kept verbatim
    assert p["current_orca_printer_profile"] == "Ghost Active"


async def test_profile_names_are_not_checked_when_the_catalog_is_unavailable(client):
    resp = await _import(client, _backup(_printer(
        "Offline", orca_printer_profiles=["Ghost Machine"], current_orca_printer_profile="Ghost Active")),
        catalog=RuntimeError("sidecar down"))

    assert resp.status_code == 200
    assert resp.json() == {"imported": 1, "skipped": 0, "warnings": []}


# ---------------------------------------------------------------------------
# Round trip and side effects
# ---------------------------------------------------------------------------

async def test_backup_then_import_round_trips_the_operator_facing_fields(client):
    filaments = [{"slot": 0, "type": "PETG", "color": "#00FF00", "name": "Generic PETG", "spoolman_spool_id": "7"}]
    created = (await client.post("/api/v1/printers", json={
        "name": "Forge", "printer_type": "elegoo_centauri", "connection_config": {"ip_address": "10.0.0.9"},
        "orca_printer_profiles": ["Known Machine"], "current_orca_printer_profile": "Known Machine",
        "loaded_filaments": filaments, "build_plate_type": "Textured PEI"})).json()
    await client.patch(f"/api/v1/printers/{created['id']}", json={"enabled": False, "queue_on": False})
    backup = (await client.get("/api/v1/settings/fleet-backup", params={"include_credentials": "true"})).json()
    await client.delete(f"/api/v1/printers/{created['id']}")
    assert await _fleet(client) == []

    resp = await _import(client, backup)

    assert resp.json() == {"imported": 1, "skipped": 0, "warnings": []}
    (p,) = await _fleet(client)
    assert (p["name"], p["printer_type"], p["connection_config"]) == ("Forge", "elegoo_centauri", {"ip_address": "10.0.0.9"})
    assert (p["orca_printer_profiles"], p["current_orca_printer_profile"]) == (["Known Machine"], "Known Machine")
    assert (p["loaded_filaments"], p["build_plate_type"]) == (filaments, "Textured PEI")
    assert (p["enabled"], p["queue_on"]) == (False, False)


async def test_imported_printers_are_registered_with_the_manager_and_a_broken_client_does_not_fail_the_import(client):
    resp = await _import(client, _backup(_printer("A"), _printer("B")))
    assert resp.json()["imported"] == 2
    assert set(printer_manager._clients) == {p["id"] for p in await _fleet(client)}

    printer_manager._clients.clear()
    with patch("app.api.routes.settings.create_client", side_effect=RuntimeError("bad config")):
        resp = await _import(client, _backup(_printer("C")))

    assert (resp.status_code, resp.json()["imported"]) == (200, 1)  # imported, just not connected
    assert printer_manager._clients == {}
    assert "C" in [p["name"] for p in await _fleet(client)]
