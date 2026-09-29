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


async def _create(client, name, **body):
    resp = await client.post("/api/v1/tags", json={"name": name, **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_tag_applies_the_documented_defaults(client):
    tag = await _create(client, "Bare")

    assert tag == {"id": tag["id"], "name": "Bare", "color": "#64748b", "category": "", "usage_count": 0}


async def test_tags_are_listed_by_category_then_name(client):
    for name, category in (("zeta", "B"), ("alpha", "B"), ("mid", "A"), ("loose", "")):
        await _create(client, name, category=category)

    names = [t["name"] for t in (await client.get("/api/v1/tags")).json()]

    assert names == ["loose", "mid", "alpha", "zeta"]  # "" < "A" < "B"; names within a category


async def test_update_tag_changes_only_the_given_fields_and_reports_usage(client):
    tag = await _create(client, "PLA", color="#111111", category="Material")

    only_color = await client.patch(f"/api/v1/tags/{tag['id']}", json={"color": "#222222"})
    renamed = await client.patch(f"/api/v1/tags/{tag['id']}", json={"name": "PLA+", "category": "Filament"})

    assert only_color.json() == {**tag, "color": "#222222"}
    assert renamed.json() == {**tag, "name": "PLA+", "color": "#222222", "category": "Filament"}
    assert [t["name"] for t in (await client.get("/api/v1/tags")).json()] == ["PLA+"]  # renamed in place


async def test_update_tag_rejects_a_name_another_tag_owns_but_allows_keeping_its_own(client):
    a = await _create(client, "PLA")
    await _create(client, "PETG")

    clash = await client.patch(f"/api/v1/tags/{a['id']}", json={"name": "PETG", "color": "#abcdef"})
    same = await client.patch(f"/api/v1/tags/{a['id']}", json={"name": "PLA", "color": "#123456"})

    assert (clash.status_code, clash.json()["detail"]) == (409, "Tag 'PETG' already exists")
    assert (same.status_code, same.json()["color"]) == (200, "#123456")
    tags = {t["name"]: t for t in (await client.get("/api/v1/tags")).json()}
    assert tags["PLA"]["color"] == "#123456" and set(tags) == {"PLA", "PETG"}  # the rejected patch changed nothing


async def test_update_and_delete_404_for_an_unknown_tag(client):
    patch_resp = await client.patch("/api/v1/tags/999", json={"color": "#000"})
    delete_resp = await client.delete("/api/v1/tags/999")

    assert (patch_resp.status_code, patch_resp.json()["detail"]) == (404, "Tag 999 not found")
    assert (delete_resp.status_code, delete_resp.json()["detail"]) == (404, "Tag 999 not found")


async def test_deleting_a_tag_detaches_it_from_every_file_but_keeps_the_files_and_other_tags(client, upload_3mf):
    file_id = await upload_3mf()
    doomed, kept = await _create(client, "Doomed"), await _create(client, "Kept")
    for tag in (doomed, kept):
        await client.post(f"/api/v1/files/{file_id}/tags", json={"tag_id": tag["id"]})
    assert (await client.get("/api/v1/tags")).json()[0]["usage_count"] == 1

    resp = await client.delete(f"/api/v1/tags/{doomed['id']}")

    assert (resp.status_code, resp.json()) == (200, {"deleted": doomed["id"]})
    (row,) = (await client.get("/api/v1/files")).json()
    assert row["id"] == file_id and [t["name"] for t in row["tags"]] == ["Kept"]
    assert [t["name"] for t in (await client.get("/api/v1/tags")).json()] == ["Kept"]
    assert (await client.get("/api/v1/files", params={"tags": ["Doomed"]})).json() == []

