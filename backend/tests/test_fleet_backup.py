"""Fleet backup/restore: credential redaction.

The backup file lands in ~/Downloads and whatever syncs it, so printer LAN
credentials (Bambu access_code, etc.) must not ride along by default.
"""
import pytest


async def _create_bambu_printer(client, access_code="00000000"):
    resp = await client.post(
        "/api/v1/printers",
        json={
            "name": "X1C",
            "printer_type": "bambu",
            "connection_config": {
                "ip_address": "192.168.1.50",
                "access_code": access_code,
                "serial_number": "SN12345",
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_backup_redacts_credentials_by_default(client):
    await _create_bambu_printer(client)
    resp = await client.get("/api/v1/settings/fleet-backup")
    assert resp.status_code == 200
    data = resp.json()
    assert data["credentials_redacted"] is True
    cfg = data["printers"][0]["connection_config"]
    assert cfg["access_code"] == ""
    # Non-secret fields are needed to make the backup useful and must survive.
    assert cfg["ip_address"] == "192.168.1.50"
    assert cfg["serial_number"] == "SN12345"


async def test_backup_include_credentials_true_keeps_them(client):
    await _create_bambu_printer(client, access_code="87654321")
    resp = await client.get("/api/v1/settings/fleet-backup", params={"include_credentials": "true"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["credentials_redacted"] is False
    assert data["printers"][0]["connection_config"]["access_code"] == "87654321"


async def test_import_of_redacted_backup_warns_about_missing_credentials(client):
    await _create_bambu_printer(client)
    backup = (await client.get("/api/v1/settings/fleet-backup")).json()

    import io
    payload = io.BytesIO(__import__("json").dumps(backup).encode())
    resp = await client.post(
        "/api/v1/settings/fleet-import",
        files={"file": ("backup.json", payload, "application/json")},
    )
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["imported"] == 1
    assert any("access_code" in w and "re-enter" in w for w in report["warnings"])


async def test_backup_carries_the_model_slicer_profile_mappings_and_the_nozzle_variant_and_import_restores_them(client):
    """BIZ-294: with profiles on the model, a restored fleet slices with the same profiles only if the mappings travel with it."""
    import io, json
    printer = await _create_bambu_printer(client)
    model = printer["model_uuid"]
    for d, name, default in ((0.4, "P1S 0.4", True), (0.6, "P1S 0.6", False)):
        assert (await client.post(f"/api/v1/printer-models/{model}/slicer-profiles", json={
            "provider_plugin_id": "laminus", "nozzle_diameter_mm": d, "machine_profile": name, "is_default": default})).status_code == 201
    await client.patch(f"/api/v1/printers/{printer['id']}", json={"nozzle_variant": {"diameter_mm": 0.6, "flow": "standard"}})

    backup = (await client.get("/api/v1/settings/fleet-backup")).json()

    assert backup["printers"][0]["nozzle_variant"] == {"diameter_mm": 0.6, "flow": "standard"}
    assert sorted((m["nozzle_diameter_mm"], m["machine_profile"], m["is_default"]) for m in backup["slicer_profiles"]) == [
        (0.4, "P1S 0.4", True), (0.6, "P1S 0.6", False)]
    assert all(m["plugin_id"] == "bambu" and m["model_id"] == "p1s" for m in backup["slicer_profiles"])

    await client.delete(f"/api/v1/printers/{printer['id']}")                                       # wipe, then restore from the file
    for m in sorted((await client.get(f"/api/v1/printer-models/{model}/slicer-profiles")).json(), key=lambda m: m["is_default"]):
        assert (await client.delete(f"/api/v1/printer-models/{model}/slicer-profiles/{m['id']}")).status_code == 204   # default last
    assert (await client.get(f"/api/v1/printer-models/{model}/slicer-profiles")).json() == []

    resp = await client.post("/api/v1/settings/fleet-import", files={"file": ("b.json", io.BytesIO(json.dumps(backup).encode()), "application/json")})

    assert resp.status_code == 200 and resp.json()["imported"] == 1, resp.text
    restored = (await client.get(f"/api/v1/printer-models/{model}/slicer-profiles")).json()
    assert sorted((m["nozzle_diameter_mm"], m["machine_profile"], m["is_default"]) for m in restored) == [(0.4, "P1S 0.4", True), (0.6, "P1S 0.6", False)]
    (imported,) = (await client.get("/api/v1/printers")).json()
    assert imported["model_uuid"] == model and imported["nozzle_variant"] == {"diameter_mm": 0.6, "flow": "standard"}


async def test_import_reads_an_old_backups_current_orca_printer_profile_as_the_raw_override(client):
    import io, json
    backup = {"themis_backup_version": 1, "printers": [{
        "name": "Old", "printer_type": "bambu", "connection_config": {}, "orca_printer_profiles": ["Old Machine"],
        "current_orca_printer_profile": "Old Machine", "loaded_filaments": []}]}
    resp = await client.post("/api/v1/settings/fleet-import", files={"file": ("b.json", io.BytesIO(json.dumps(backup).encode()), "application/json")})
    assert resp.status_code == 200 and resp.json()["imported"] == 1
    (p,) = (await client.get("/api/v1/printers")).json()
    assert p["machine_profile_override"] == "Old Machine" and p["model_uuid"]
