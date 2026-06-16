from httpx import AsyncClient


async def test_get_queue_config_operator_name_null_on_fresh_row(client: AsyncClient):
    resp = await client.get("/api/v1/settings/queue")

    assert resp.status_code == 200
    body = resp.json()
    assert body["operator_name"] is None
    assert body["check_interval_minutes"] == 5


async def test_put_operator_name_only_leaves_check_interval_untouched(client: AsyncClient):
    await client.put("/api/v1/settings/queue", json={"check_interval_minutes": 10})

    resp = await client.put("/api/v1/settings/queue", json={"operator_name": "Workshop Lead"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["operator_name"] == "Workshop Lead"
    assert body["check_interval_minutes"] == 10


async def test_put_check_interval_only_leaves_operator_name_untouched(client: AsyncClient):
    await client.put("/api/v1/settings/queue", json={"operator_name": "Workshop Lead"})

    resp = await client.put("/api/v1/settings/queue", json={"check_interval_minutes": 15})

    assert resp.status_code == 200
    body = resp.json()
    assert body["check_interval_minutes"] == 15
    assert body["operator_name"] == "Workshop Lead"


async def test_put_empty_operator_name_clears_it_to_null(client: AsyncClient):
    await client.put("/api/v1/settings/queue", json={"operator_name": "Workshop Lead"})

    resp = await client.put("/api/v1/settings/queue", json={"operator_name": ""})

    assert resp.status_code == 200
    assert resp.json()["operator_name"] is None
