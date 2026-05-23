# backend/tests/api/test_projects_api.py
import pytest


async def test_create_project(client):
    response = await client.post("/api/v1/projects", json={"name": "My Project", "description": "desc"})
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "My Project"
    assert data["id"] is not None


async def test_list_projects_empty(client):
    response = await client.get("/api/v1/projects")
    assert response.status_code == 200
    assert response.json() == []


async def test_get_project(client):
    create = await client.post("/api/v1/projects", json={"name": "P1"})
    pid = create.json()["id"]
    response = await client.get(f"/api/v1/projects/{pid}")
    assert response.status_code == 200
    assert response.json()["id"] == pid


async def test_get_project_not_found(client):
    response = await client.get("/api/v1/projects/9999")
    assert response.status_code == 404


async def test_delete_project(client):
    create = await client.post("/api/v1/projects", json={"name": "Temp"})
    pid = create.json()["id"]
    response = await client.delete(f"/api/v1/projects/{pid}")
    assert response.status_code == 204
    response = await client.get(f"/api/v1/projects/{pid}")
    assert response.status_code == 404
