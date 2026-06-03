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
async def test_move_to_same_folder_is_noop(client, lib):
    # Moving a file to the folder it already lives in must NOT suffix-rename it.
    up = (await client.post("/api/v1/files/upload", data={"folder": "/Customers"}, files=_stl("a.stl"))).json()
    r = await client.patch(f"/api/v1/files/{up['id']}", json={"folder": "/Customers"})
    assert r.status_code == 200, r.text
    assert r.json()["original_filename"] == "a.stl"          # not "a (2).stl"
    assert (lib / "Customers" / "a.stl").is_file()
    assert not (lib / "Customers" / "a (2).stl").exists()


@pytest.mark.asyncio
async def test_dirs_includes_empty_folders(client, lib):
    # An empty folder created on disk must appear in /dirs (it won't in /tree).
    await client.post("/api/v1/files/folders", json={"path": "/Empty/Nested"})
    await client.post("/api/v1/files/upload", data={"folder": "/Customers"}, files=_stl("a.stl"))
    r = await client.get("/api/v1/files/dirs")
    assert r.status_code == 200, r.text
    tree = r.json()
    assert "Empty" in tree["children"]
    assert "Nested" in tree["children"]["Empty"]["children"]
    assert tree["children"]["Empty"]["path"] == "/Empty"
    # file count overlaid on the folder that has a file
    assert tree["children"]["Customers"]["count"] == 1
    # /tree (index-derived) must NOT contain the empty folder
    t = (await client.get("/api/v1/files/tree")).json()
    assert "Empty" not in t["children"]


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


@pytest.mark.asyncio
async def test_move_rejects_name_traversal(client, lib):
    """PATCH with a traversal name must not move the file outside the library."""
    up = (await client.post("/api/v1/files/upload", files=_stl("victim.stl"))).json()
    file_id = up["id"]

    # Craft a name that would escape the library root via path traversal.
    r = await client.patch(f"/api/v1/files/{file_id}", json={"name": "../../../escape.stl"})

    # The response must NOT be a 500 (unhandled error).
    assert r.status_code != 500, f"Unhandled 500 means traversal reached os.replace: {r.text}"

    # The escaped file must not exist anywhere outside the library.
    assert not (lib.parent / "escape.stl").exists(), "File escaped one level above library!"
    assert not (lib.parent.parent / "escape.stl").exists(), "File escaped two levels above library!"

    # The original file must still be accessible (either unchanged or safely renamed inside lib).
    r2 = await client.get(f"/api/v1/files/{file_id}")
    if r.status_code == 400:
        # Fix path: file was rejected, original still intact inside lib
        assert r2.status_code == 200, "File record should still exist after rejected rename"
        stored_path = r2.json()["relative_path"]
        assert not stored_path.startswith(".."), "Stored path must not escape via .."
        # Confirm the physical file is still inside the library
        import pathlib
        full = lib / stored_path.lstrip("/")
        assert full.exists(), "Original file must still be present inside the library"
    else:
        # Fix applied basename-stripping: name was sanitized, file stays inside lib
        assert r.status_code == 200, f"Expected 200 or 400, got {r.status_code}: {r.text}"
        stored_path = r.json()["relative_path"]
        full = lib / stored_path.lstrip("/")
        assert full.exists(), "Renamed file must still be inside the library"
        assert lib.resolve() in full.resolve().parents or full.resolve().parent == lib.resolve(), \
            "Renamed file escaped the library root"


@pytest.mark.asyncio
async def test_move_rejects_folder_traversal(client, lib):
    """PATCH with a traversal folder must return 400 and not move the file outside the library."""
    up = (await client.post("/api/v1/files/upload", files=_stl("safe.stl"))).json()
    file_id = up["id"]

    r = await client.patch(f"/api/v1/files/{file_id}", json={"folder": "../../etc"})

    # Must be rejected with 400 (existing _safe_subpath guard).
    assert r.status_code == 400, f"Expected 400 for folder traversal, got {r.status_code}: {r.text}"

    # The file must not have escaped — confirm it's still inside lib.
    escaped_dir = lib.parent.parent / "etc"
    assert not (escaped_dir / "safe.stl").exists(), "File escaped to ../../etc!"
