import pytest


@pytest.mark.asyncio
async def test_tags_crud(client):
    r = await client.post("/api/v1/tags", json={"name": "PLA", "color": "#22c55e", "category": "Material"})
    assert r.status_code == 201, r.text
    tag = r.json()
    assert tag["name"] == "PLA" and tag["usage_count"] == 0

    r = await client.get("/api/v1/tags")
    assert r.status_code == 200
    assert any(t["name"] == "PLA" for t in r.json())

    r = await client.patch(f"/api/v1/tags/{tag['id']}", json={"color": "#000000"})
    assert r.status_code == 200 and r.json()["color"] == "#000000"

    r = await client.delete(f"/api/v1/tags/{tag['id']}")
    assert r.status_code == 200
    r = await client.get("/api/v1/tags")
    assert all(t["name"] != "PLA" for t in r.json())


@pytest.mark.asyncio
async def test_duplicate_tag_name_409(client):
    await client.post("/api/v1/tags", json={"name": "PETG", "color": "#fff", "category": ""})
    r = await client.post("/api/v1/tags", json={"name": "PETG", "color": "#000", "category": ""})
    assert r.status_code == 409
