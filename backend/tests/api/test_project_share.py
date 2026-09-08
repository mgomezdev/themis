from httpx import AsyncClient


async def _create_project(client: AsyncClient) -> int:
    resp = await client.post("/api/v1/projects", json={"name": "Shareable Project"})
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_get_share_state_disabled_by_default(client: AsyncClient):
    project_id = await _create_project(client)

    resp = await client.get(f"/api/v1/projects/{project_id}/share")

    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "token": None}


async def test_put_share_creates_a_token(client: AsyncClient):
    project_id = await _create_project(client)

    resp = await client.put(f"/api/v1/projects/{project_id}/share")

    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert isinstance(body["token"], str) and len(body["token"]) > 20


async def test_get_share_state_reflects_created_token(client: AsyncClient):
    project_id = await _create_project(client)
    created = (await client.put(f"/api/v1/projects/{project_id}/share")).json()

    resp = await client.get(f"/api/v1/projects/{project_id}/share")

    assert resp.json() == created


async def test_put_share_again_regenerates_a_different_token(client: AsyncClient):
    project_id = await _create_project(client)
    first = (await client.put(f"/api/v1/projects/{project_id}/share")).json()

    second = (await client.put(f"/api/v1/projects/{project_id}/share")).json()

    assert second["token"] != first["token"]


async def test_delete_share_revokes_the_token(client: AsyncClient):
    project_id = await _create_project(client)
    await client.put(f"/api/v1/projects/{project_id}/share")

    resp = await client.delete(f"/api/v1/projects/{project_id}/share")

    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "token": None}


async def test_share_endpoints_404_for_missing_project(client: AsyncClient):
    assert (await client.get("/api/v1/projects/999999/share")).status_code == 404
    assert (await client.put("/api/v1/projects/999999/share")).status_code == 404
    assert (await client.delete("/api/v1/projects/999999/share")).status_code == 404


async def test_share_endpoints_require_projects_share_scope_not_just_write(client: AsyncClient):
    """A key scoped to projects:write (but not projects:share) must not be able to
    manage share links - minting one via the real /api/v1/api-keys endpoint (the
    fixture's own key has every scope, including apikeys:write) rather than reaching
    into test internals."""
    project_id = await _create_project(client)

    create_resp = await client.post("/api/v1/api-keys", json={
        "name": "write-only", "scopes": ["projects:read", "projects:write"],
    })
    assert create_resp.status_code == 200
    write_only_key = create_resp.json()["key"]

    resp = await client.get(
        f"/api/v1/projects/{project_id}/share", headers={"X-Api-Key": write_only_key},
    )
    assert resp.status_code == 403
