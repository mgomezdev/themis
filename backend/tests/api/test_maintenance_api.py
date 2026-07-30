import pytest


@pytest.mark.asyncio
async def test_create_and_list_general_item(client):
    r = await client.post("/api/v1/maintenance/items", json={
        "name": "Wash build plate",
        "scope": "general",
        "triggers": [{"trigger_type": "job_count", "amount": 10, "unit": None}],
    })
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["name"] == "Wash build plate"
    assert item["scope"] == "general"
    assert len(item["triggers"]) == 1
    assert item["triggers"][0]["trigger_type"] == "job_count"

    r = await client.get("/api/v1/maintenance/items")
    assert r.status_code == 200
    assert any(i["name"] == "Wash build plate" for i in r.json())


@pytest.mark.asyncio
async def test_create_model_scoped_item_requires_vendor_and_model(client):
    r = await client.post("/api/v1/maintenance/items", json={
        "name": "AMS desiccant", "scope": "model", "triggers": [],
    })
    assert r.status_code == 422

    r = await client.post("/api/v1/maintenance/items", json={
        "name": "AMS desiccant", "scope": "model",
        "machine_vendor": "Bambu Lab", "machine_model": "X1 Carbon",
        "triggers": [{"trigger_type": "calendar", "amount": 2, "unit": "months"}],
    })
    assert r.status_code == 201, r.text
    assert r.json()["machine_vendor"] == "Bambu Lab"


@pytest.mark.asyncio
async def test_update_item_patches_fields(client):
    r = await client.post("/api/v1/maintenance/items", json={
        "name": "Clean fans", "scope": "general",
        "triggers": [{"trigger_type": "calendar", "amount": 3, "unit": "months"}],
    })
    item_id = r.json()["id"]

    r = await client.patch(f"/api/v1/maintenance/items/{item_id}", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


@pytest.mark.asyncio
async def test_update_missing_item_404(client):
    r = await client.patch("/api/v1/maintenance/items/999", json={"enabled": False})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_scope_to_model_without_vendor_and_model_422(client):
    r = await client.post("/api/v1/maintenance/items", json={
        "name": "Lube rods", "scope": "general",
        "triggers": [{"trigger_type": "calendar", "amount": 6, "unit": "months"}],
    })
    item_id = r.json()["id"]

    r = await client.patch(f"/api/v1/maintenance/items/{item_id}", json={"scope": "model"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_scope_to_general_clears_vendor_and_model(client):
    r = await client.post("/api/v1/maintenance/items", json={
        "name": "Nozzle swap", "scope": "model",
        "machine_vendor": "Prusa", "machine_model": "MK4",
        "triggers": [{"trigger_type": "job_count", "amount": 200, "unit": None}],
    })
    item_id = r.json()["id"]

    r = await client.patch(f"/api/v1/maintenance/items/{item_id}", json={"scope": "general"})
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "general"
    assert body["machine_vendor"] is None
    assert body["machine_model"] is None


@pytest.mark.asyncio
async def test_create_item_invalid_trigger_type_422(client):
    r = await client.post("/api/v1/maintenance/items", json={
        "name": "Bad trigger", "scope": "general",
        "triggers": [{"trigger_type": "weekly", "amount": 1, "unit": None}],
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_delete_item_cascades(client):
    r = await client.post("/api/v1/maintenance/items", json={
        "name": "Check belts", "scope": "general",
        "triggers": [{"trigger_type": "calendar", "amount": 2, "unit": "months"}],
    })
    item_id = r.json()["id"]

    r = await client.delete(f"/api/v1/maintenance/items/{item_id}")
    assert r.status_code == 200
    assert r.json()["deleted"] == item_id

    r = await client.get("/api/v1/maintenance/items")
    assert all(i["id"] != item_id for i in r.json())
