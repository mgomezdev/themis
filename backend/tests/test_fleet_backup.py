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
