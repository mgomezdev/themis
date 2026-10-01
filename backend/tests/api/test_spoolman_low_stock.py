"""GET/PUT /api/v1/spoolman/low-stock"""
import pytest


async def test_defaults_to_no_alerts(client):
    assert (await client.get("/api/v1/spoolman/low-stock")).json() == {"default_g": None, "overrides": {}}


async def test_put_roundtrips_default_and_per_filament_overrides_and_replaces_them(client):
    r = await client.put("/api/v1/spoolman/low-stock", json={"default_g": 150, "overrides": {"3": 40.5, "12": 0}})
    assert r.status_code == 200
    assert (await client.get("/api/v1/spoolman/low-stock")).json() == {"default_g": 150.0, "overrides": {"3": 40.5, "12": 0.0}}

    await client.put("/api/v1/spoolman/low-stock", json={"default_g": None, "overrides": {"7": 10}})
    assert (await client.get("/api/v1/spoolman/low-stock")).json() == {"default_g": None, "overrides": {"7": 10.0}}


@pytest.mark.parametrize("body", [
    {"default_g": -1}, {"default_g": 1e9}, {"overrides": {"abc": 5}}, {"overrides": {"3": -5}}, {"overrides": {"3": 1e9}},
    {"overrides": {"": 5}},
])
async def test_invalid_thresholds_are_422_and_change_nothing(client, body):
    await client.put("/api/v1/spoolman/low-stock", json={"default_g": 50, "overrides": {}})
    assert (await client.put("/api/v1/spoolman/low-stock", json=body)).status_code == 422
    assert (await client.get("/api/v1/spoolman/low-stock")).json()["default_g"] == 50.0


async def test_saving_thresholds_keeps_the_existing_spoolman_connection_settings(client):
    await client.put("/api/v1/settings/spoolman", json={"enabled": True, "url": "http://sm.test", "api_key": "k"})
    await client.put("/api/v1/spoolman/low-stock", json={"default_g": 80, "overrides": {}})

    cfg = (await client.get("/api/v1/settings/spoolman")).json()
    assert (cfg["enabled"], cfg["url"], cfg["has_api_key"]) == (True, "http://sm.test", True)
