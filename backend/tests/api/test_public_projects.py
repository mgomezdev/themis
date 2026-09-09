from unittest.mock import patch

from httpx import AsyncClient


def _make_stl_bytes() -> bytes:
    return b"solid part\nendsolid"


async def _create_shared_project(client: AsyncClient) -> tuple[int, str]:
    resp = await client.post("/api/v1/projects", json={
        "name": "Public Project", "customer": "Acme Co", "due_date": "2026-12-01",
        "notes": "internal note that must never appear on the public page",
    })
    project_id = resp.json()["id"]
    share = (await client.put(f"/api/v1/projects/{project_id}/share")).json()
    return project_id, share["token"]


async def test_public_project_returns_trimmed_shape(client: AsyncClient):
    project_id, token = await _create_shared_project(client)

    resp = await client.get(f"/api/v1/public/projects/{token}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Public Project"
    assert body["customer"] == "Acme Co"
    assert body["due_date"] == "2026-12-01"
    assert body["on_hold"] is False
    assert body["items"] == []
    assert body["parts"] == []
    assert body["links"] == []
    assert body["jobs_total"] == 0
    assert body["jobs_complete"] == 0
    assert body["estimate_seconds_remaining"] is None
    assert "updated_at" in body


async def test_public_project_excludes_internal_fields(client: AsyncClient):
    project_id, token = await _create_shared_project(client)

    resp = await client.get(f"/api/v1/public/projects/{token}")

    body = resp.json()
    for internal_field in (
        "notes", "source_app", "source_user", "source_layout_id",
        "machine_uuid", "process_uuid", "order_id", "result_file_id", "id",
    ):
        assert internal_field not in body


async def test_public_project_404_for_unknown_token(client: AsyncClient):
    resp = await client.get("/api/v1/public/projects/not-a-real-token")
    assert resp.status_code == 404


async def test_public_project_404_after_revoke(client: AsyncClient):
    project_id, token = await _create_shared_project(client)
    await client.delete(f"/api/v1/projects/{project_id}/share")

    resp = await client.get(f"/api/v1/public/projects/{token}")

    assert resp.status_code == 404


async def test_public_project_old_token_404s_after_regenerate(client: AsyncClient):
    project_id, token = await _create_shared_project(client)

    await client.put(f"/api/v1/projects/{project_id}/share")  # regenerate

    resp = await client.get(f"/api/v1/public/projects/{token}")

    assert resp.status_code == 404


async def test_public_project_requires_no_api_key(client: AsyncClient):
    """The public endpoint must work with zero auth headers at all - a plain,
    unauthenticated client, not just the fixture's pre-keyed one."""
    from httpx import AsyncClient as PlainClient, ASGITransport
    from app.main import app

    project_id, token = await _create_shared_project(client)

    async with PlainClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        resp = await anon.get(f"/api/v1/public/projects/{token}")

    assert resp.status_code == 200
    assert resp.json()["name"] == "Public Project"


async def test_public_project_includes_item_and_part_and_link_summaries(client: AsyncClient, tmp_path):
    project_id, token = await _create_shared_project(client)

    lib = tmp_path / "library"
    lib.mkdir(exist_ok=True)
    (tmp_path / "filecache").mkdir(exist_ok=True)
    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
    ):
        upload = await client.post("/api/v1/files/upload", files={
            "file": ("part.stl", _make_stl_bytes(), "application/octet-stream"),
        })
        assert upload.status_code == 201
        file_id = upload.json()["id"]

        await client.post(f"/api/v1/projects/{project_id}/items", json={"file_id": file_id, "quantity": 3})
        await client.post(f"/api/v1/projects/{project_id}/parts", json={"name": "M3 bolt", "quantity": 4})
        await client.post(f"/api/v1/projects/{project_id}/links", json={"url": "https://example.com", "label": "Spec sheet"})

        resp = await client.get(f"/api/v1/public/projects/{token}")

    body = resp.json()
    assert body["items"] == [{"name": "part.stl", "quantity": 3, "quantity_completed": 0}]
    assert body["parts"] == [{"name": "M3 bolt", "quantity": 4}]
    assert body["links"] == [{"url": "https://example.com", "label": "Spec sheet"}]
