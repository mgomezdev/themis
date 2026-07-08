import pytest


async def test_create_project_without_machine_process(client):
    """Projects can be created without machine_uuid / process_uuid for external importers."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Ordinus Import"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Ordinus Import"
    assert data["machine_uuid"] is None
    assert data["process_uuid"] is None


async def test_create_project_with_source_fields(client):
    """Source fields are stored and returned on the project."""
    resp = await client.post(
        "/api/v1/projects",
        json={
            "name": "Ordinus Import",
            "source_app": "ordinus",
            "source_user": "alice",
            "source_layout_id": 42,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["source_app"] == "ordinus"
    assert data["source_user"] == "alice"
    assert data["source_layout_id"] == 42


async def test_create_project_source_fields_default_null(client):
    """Source fields are null when not provided."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Regular Project", "machine_uuid": "abc", "process_uuid": "def"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["source_app"] is None
    assert data["source_user"] is None
    assert data["source_layout_id"] is None
