import io
import pytest
from app import config


@pytest.fixture
def lib(tmp_path, monkeypatch):
    library = tmp_path / "library"; library.mkdir()
    cache = tmp_path / "filecache"; cache.mkdir()
    monkeypatch.setattr(config, "get_library_dir", lambda: library)
    monkeypatch.setattr(config, "get_filecache_dir", lambda: cache)
    return library


def _stl(name="part.stl"):
    return {"file": (name, io.BytesIO(b"solid x\nendsolid x\n"), "application/octet-stream")}


@pytest.mark.asyncio
async def test_upload_lands_in_default_folder(client, lib):
    r = await client.post("/api/v1/files/upload", files=_stl())
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["folder"] == "/Job Uploads"
    assert (lib / "Job Uploads" / "part.stl").is_file()


@pytest.mark.asyncio
async def test_upload_to_named_folder_and_list(client, lib):
    await client.post("/api/v1/files/upload", data={"folder": "/Customers/Vela"}, files=_stl("arm.stl"))
    r = await client.get("/api/v1/files", params={"folder": "/Customers/Vela"})
    assert r.status_code == 200
    names = [f["original_filename"] for f in r.json()]
    assert "arm.stl" in names


@pytest.mark.asyncio
async def test_tag_assign_filter(client, lib):
    up = (await client.post("/api/v1/files/upload", files=_stl("a.stl"))).json()
    tag = (await client.post("/api/v1/tags", json={"name": "PLA", "color": "#fff", "category": "Material"})).json()
    r = await client.post(f"/api/v1/files/{up['id']}/tags", json={"tag_id": tag["id"]})
    assert r.status_code == 200
    r = await client.get("/api/v1/files", params={"tags": ["PLA"]})
    assert any(f["id"] == up["id"] for f in r.json())
    assert up["id"] in [f["id"] for f in r.json()]


@pytest.mark.asyncio
async def test_rename_move_keeps_tags(client, lib):
    up = (await client.post("/api/v1/files/upload", files=_stl("a.stl"))).json()
    tag = (await client.post("/api/v1/tags", json={"name": "x", "color": "#fff", "category": ""})).json()
    await client.post(f"/api/v1/files/{up['id']}/tags", json={"tag_id": tag["id"]})
    r = await client.patch(f"/api/v1/files/{up['id']}", json={"folder": "/Archive", "name": "renamed.stl"})
    assert r.status_code == 200, r.text
    assert (lib / "Archive" / "renamed.stl").is_file()
    assert not (lib / "Job Uploads" / "a.stl").exists()
    r = await client.get("/api/v1/files", params={"tags": ["x"]})
    assert up["id"] in [f["id"] for f in r.json()]


@pytest.mark.asyncio
async def test_delete_blocked_by_active_job(client, lib):
    up = (await client.post("/api/v1/files/upload", files=_stl("a.stl"))).json()
    # Seed an active job referencing the file via the get_session override.
    from app.main import app
    from app.database import get_session
    from app.models import Job
    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    session.add(Job(uploaded_file_id=up["id"], plate_number=1, status="printing",
                    created_at="t", updated_at="t"))
    await session.commit()
    r = await client.delete(f"/api/v1/files/{up['id']}")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_create_folder_and_tree(client, lib):
    r = await client.post("/api/v1/files/folders", json={"path": "/Customers/New"})
    assert r.status_code == 201
    assert (lib / "Customers" / "New").is_dir()
    r = await client.get("/api/v1/files/tree")
    assert r.status_code == 200
