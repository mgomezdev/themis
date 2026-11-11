"""BIZ-358: a printer's default process profile (validated against its machine's compatible presets) and "single colour only"."""
from tests.printer_ips import ip_config
from unittest.mock import patch

from tests.catalog_helpers import catalog_from_dict

_MACHINE = "Bambu Lab P1S 0.4 nozzle"
_CAT = catalog_from_dict({
    "machine": [{"name": _MACHINE, "uuid": "m-1"}],
    "process": [
        {"name": "0.20mm Standard", "uuid": "p-1", "compatible_printers": [_MACHINE]},
        {"name": "0.12mm Fine @Other", "uuid": "p-2", "compatible_printers": ["Some Other Printer"]},
    ],
    "filament": [],
})


async def _printer(client) -> int:
    r = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu", "connection_config": ip_config(), "machine_profile_override": _MACHINE})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["default_process_profile"] is None and body["single_colour_only"] is False
    return body["id"]


async def test_default_process_profile_roundtrip_and_clear(client):
    pid = await _printer(client)
    with patch("app.api.routes.printers._fetch_sidecar_catalog", return_value=_CAT):
        r = await client.patch(f"/api/v1/printers/{pid}", json={"default_process_profile": "0.20mm Standard"})
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/v1/printers/{pid}")).json()["default_process_profile"] == "0.20mm Standard"

    r = await client.patch(f"/api/v1/printers/{pid}", json={"default_process_profile": None})
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/v1/printers/{pid}")).json()["default_process_profile"] is None


async def test_default_process_profile_must_be_compatible(client):
    pid = await _printer(client)
    with patch("app.api.routes.printers._fetch_sidecar_catalog", return_value=_CAT):
        r = await client.patch(f"/api/v1/printers/{pid}", json={"default_process_profile": "0.12mm Fine @Other"})
    assert r.status_code == 422
    assert "0.12mm Fine @Other" in r.json()["detail"]
    assert (await client.get(f"/api/v1/printers/{pid}")).json()["default_process_profile"] is None


async def test_default_process_profile_unverifiable_without_catalog(client):
    pid = await _printer(client)
    with patch("app.api.routes.printers._fetch_sidecar_catalog", return_value=None):
        r = await client.patch(f"/api/v1/printers/{pid}", json={"default_process_profile": "0.20mm Standard"})
    assert r.status_code == 422
    assert (await client.get(f"/api/v1/printers/{pid}")).json()["default_process_profile"] is None


async def test_single_colour_only_roundtrip(client):
    pid = await _printer(client)
    r = await client.patch(f"/api/v1/printers/{pid}", json={"single_colour_only": True})
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/v1/printers/{pid}")).json()["single_colour_only"] is True
    r = await client.patch(f"/api/v1/printers/{pid}", json={"name": "renamed"})
    assert (await client.get(f"/api/v1/printers/{pid}")).json()["single_colour_only"] is True   # omitted = unchanged


async def test_an_unrelated_edit_does_not_revalidate_the_stored_default(client):
    """The editor always sends the field back: with the catalog down, saving a rename must not 422 on the unchanged default."""
    pid = await _printer(client)
    with patch("app.api.routes.printers._fetch_sidecar_catalog", return_value=_CAT):
        assert (await client.patch(f"/api/v1/printers/{pid}", json={"default_process_profile": "0.20mm Standard"})).status_code == 200
    with patch("app.api.routes.printers._fetch_sidecar_catalog", return_value=None):
        r = await client.patch(f"/api/v1/printers/{pid}", json={"name": "Renamed", "default_process_profile": "0.20mm Standard"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Renamed" and r.json()["default_process_profile"] == "0.20mm Standard"


async def test_create_validates_the_default_too(client):
    body = {"name": "P1S", "printer_type": "bambu", "connection_config": ip_config(), "machine_profile_override": _MACHINE}
    with patch("app.api.routes.printers._fetch_sidecar_catalog", return_value=_CAT):
        bad = await client.post("/api/v1/printers", json={**body, "default_process_profile": "0.12mm Fine @Other"})
        good = await client.post("/api/v1/printers", json={**body, "default_process_profile": "0.20mm Standard"})
    assert bad.status_code == 422
    assert good.status_code == 201 and good.json()["default_process_profile"] == "0.20mm Standard"
