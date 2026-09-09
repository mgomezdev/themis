from httpx import AsyncClient


async def _create_project(client: AsyncClient) -> int:
    resp = await client.post("/api/v1/projects", json={"name": "Shareable Project"})
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_get_share_state_disabled_by_default(client: AsyncClient):
    project_id = await _create_project(client)

    resp = await client.get(f"/api/v1/projects/{project_id}/share")

    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "token": None, "created_at": None}


async def test_put_share_creates_a_token(client: AsyncClient):
    project_id = await _create_project(client)

    resp = await client.put(f"/api/v1/projects/{project_id}/share")

    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert isinstance(body["token"], str) and len(body["token"]) > 20
    assert isinstance(body["created_at"], str) and body["created_at"]


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
    assert resp.json() == {"enabled": False, "token": None, "created_at": None}


async def test_share_endpoints_404_for_missing_project(client: AsyncClient):
    assert (await client.get("/api/v1/projects/999999/share")).status_code == 404
    assert (await client.put("/api/v1/projects/999999/share")).status_code == 404
    assert (await client.delete("/api/v1/projects/999999/share")).status_code == 404


async def test_share_endpoints_reject_projects_write_only_key(client: AsyncClient):
    """A key scoped to projects:write (but not projects:share) must not be able to
    manage share links on any of the three endpoints - minting one via the real
    /api/v1/api-keys endpoint (the fixture's own key has every scope, including
    apikeys:write) rather than reaching into test internals."""
    project_id = await _create_project(client)

    create_resp = await client.post("/api/v1/api-keys", json={
        "name": "write-only", "scopes": ["projects:read", "projects:write"],
    })
    assert create_resp.status_code == 200
    write_only_key = create_resp.json()["key"]
    headers = {"X-Api-Key": write_only_key}

    assert (await client.get(f"/api/v1/projects/{project_id}/share", headers=headers)).status_code == 403
    assert (await client.put(f"/api/v1/projects/{project_id}/share", headers=headers)).status_code == 403
    assert (await client.delete(f"/api/v1/projects/{project_id}/share", headers=headers)).status_code == 403


async def test_share_endpoints_accept_projects_share_only_key(client: AsyncClient):
    """The inverse of the above: a key scoped to ONLY projects:share (no
    projects:read/write) must still succeed on all three endpoints."""
    project_id = await _create_project(client)

    create_resp = await client.post("/api/v1/api-keys", json={
        "name": "share-only", "scopes": ["projects:share"],
    })
    assert create_resp.status_code == 200
    share_only_key = create_resp.json()["key"]
    headers = {"X-Api-Key": share_only_key}

    assert (await client.get(f"/api/v1/projects/{project_id}/share", headers=headers)).status_code == 200
    assert (await client.put(f"/api/v1/projects/{project_id}/share", headers=headers)).status_code == 200
    assert (await client.delete(f"/api/v1/projects/{project_id}/share", headers=headers)).status_code == 200


async def test_put_share_retries_once_on_token_collision(client: AsyncClient, monkeypatch):
    """The astronomically unlikely case of a fresh token colliding with an existing
    one must not surface as a 500 - retry once with a new token."""
    project_a_id = await _create_project(client)
    existing_token = (await client.put(f"/api/v1/projects/{project_a_id}/share")).json()["token"]

    project_b_id = await _create_project(client)

    calls = {"n": 0}

    def fake_token_urlsafe(nbytes):
        calls["n"] += 1
        return existing_token if calls["n"] == 1 else "unique-second-token"

    monkeypatch.setattr("app.api.routes.projects.secrets.token_urlsafe", fake_token_urlsafe)

    resp = await client.put(f"/api/v1/projects/{project_b_id}/share")

    assert resp.status_code == 200
    assert resp.json()["token"] == "unique-second-token"
    assert calls["n"] == 2
