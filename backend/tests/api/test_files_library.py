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


def _stl_with(name: str, content: bytes):
    """A DIFFERENT file: uploads with identical bytes are deduplicated into one row."""
    return {"file": (name, io.BytesIO(content), "application/octet-stream")}


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
async def test_delete_empty_folder(client, lib):
    await client.post("/api/v1/files/folders", json={"path": "/Trash"})
    assert (lib / "Trash").is_dir()
    r = await client.delete("/api/v1/files/folders", params={"path": "/Trash"})
    assert r.status_code == 200, r.text
    assert not (lib / "Trash").exists()


@pytest.mark.asyncio
async def test_delete_nonempty_folder_409(client, lib):
    await client.post("/api/v1/files/upload", data={"folder": "/Keep"}, files=_stl("a.stl"))
    r = await client.delete("/api/v1/files/folders", params={"path": "/Keep"})
    assert r.status_code == 409
    assert (lib / "Keep").is_dir()  # still present


@pytest.mark.asyncio
async def test_delete_root_folder_rejected(client, lib):
    await client.post("/api/v1/files/upload", data={"folder": "/Keep"}, files=_stl("a.stl"))
    before = (await client.get("/api/v1/files/dirs")).json()

    r = await client.delete("/api/v1/files/folders", params={"path": "/"})

    assert r.status_code == 400
    assert lib.is_dir() and (lib / "Keep" / "a.stl").is_file()  # library untouched
    assert (await client.get("/api/v1/files/dirs")).json() == before
    assert [f["original_filename"] for f in (await client.get("/api/v1/files")).json()] == ["a.stl"]


@pytest.mark.asyncio
async def test_delete_job_uploads_rejected(client, lib):
    (lib / "Job Uploads").mkdir()
    r = await client.delete("/api/v1/files/folders", params={"path": "/Job Uploads"})
    assert r.status_code == 400
    assert (lib / "Job Uploads").is_dir()


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
    tagged = (await client.post("/api/v1/files/upload", files=_stl("a.stl"))).json()
    other = (await client.post("/api/v1/files/upload", files=_stl_with("b.stl", b"solid b\nendsolid b\n"))).json()
    pla = (await client.post("/api/v1/tags", json={"name": "PLA", "color": "#fff", "category": "Material"})).json()
    red = (await client.post("/api/v1/tags", json={"name": "Red", "color": "#f00", "category": "Colour"})).json()
    for tag in (pla, red):
        assert (await client.post(f"/api/v1/files/{tagged['id']}/tags", json={"tag_id": tag["id"]})).status_code == 200
    assert (await client.post(f"/api/v1/files/{other['id']}/tags", json={"tag_id": pla["id"]})).status_code == 200

    async def ids(*names):
        return sorted(f["id"] for f in (await client.get("/api/v1/files", params={"tags": list(names)})).json())

    assert await ids("PLA") == sorted([tagged["id"], other["id"]])   # one tag: everything carrying it
    assert await ids("PLA", "Red") == [tagged["id"]]                  # several tags: ALL must be present
    assert await ids("Red") == [tagged["id"]]
    assert await ids("NoSuchTag") == []                               # an unknown tag matches nothing
    assert await ids() == sorted([tagged["id"], other["id"]])         # no filter: everything


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
    assert [f["id"] for f in r.json()] == [up["id"]]
    other = (await client.post("/api/v1/files/upload", files=_stl_with("other.stl", b"solid o\nendsolid o\n"))).json()
    assert other["id"] not in [f["id"] for f in (await client.get("/api/v1/files", params={"tags": ["x"]})).json()]


@pytest.mark.asyncio
async def test_delete_blocked_by_active_job(client, lib, session_factory):
    up = (await client.post("/api/v1/files/upload", files=_stl("a.stl"))).json()
    # Seed an active job referencing the file.
    from app.models import Job
    async with session_factory() as session:
        session.add(Job(uploaded_file_id=up["id"], plate_number=1, status="printing",
                        created_at="t", updated_at="t"))
        await session.commit()
    r = await client.delete(f"/api/v1/files/{up['id']}")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_upload_does_not_persist_absolute_path(client, lib, session_factory):
    """stored_path must stay blank for library-indexed files - an absolute path
    written by one execution context (local dev) is not valid read back in another
    (the container), so relative_path is the only path persisted."""
    from app.models import UploadedFile

    up = (await client.post("/api/v1/files/upload", files=_stl("a.stl"))).json()
    async with session_factory() as session:
        row = await session.get(UploadedFile, up["id"])
        assert row.stored_path == ""
        assert row.relative_path == "Job Uploads/a.stl"


@pytest.mark.asyncio
async def test_download_and_model_filaments_work_without_stored_path(client, lib):
    """Read paths must resolve the file purely from relative_path + the current
    library root, with stored_path blank."""
    up = (await client.post("/api/v1/files/upload", files=_stl("a.stl"))).json()
    r = await client.get(f"/api/v1/files/{up['id']}/download")
    assert r.status_code == 200
    assert r.content == b"solid x\nendsolid x\n"
    # model-filaments resolves the same path: a geometry-only STL simply declares no filaments
    r = await client.get(f"/api/v1/files/{up['id']}/model-filaments")
    assert (r.status_code, r.json()) == (200, [])


@pytest.mark.asyncio
async def test_rename_move_resolves_source_without_stored_path(client, lib):
    """update_file() locates the file to move via relative_path, not the (blank)
    stored_path column."""
    up = (await client.post("/api/v1/files/upload", files=_stl("a.stl"))).json()
    r = await client.patch(f"/api/v1/files/{up['id']}", json={"folder": "/Moved"})
    assert r.status_code == 200, r.text
    assert (lib / "Moved" / "a.stl").is_file()
    assert not (lib / "Job Uploads" / "a.stl").exists()


@pytest.mark.asyncio
async def test_delete_succeeds_and_removes_file_from_disk(client, lib):
    up = (await client.post("/api/v1/files/upload", files=_stl("a.stl"))).json()
    assert (lib / "Job Uploads" / "a.stl").is_file()
    r = await client.delete(f"/api/v1/files/{up['id']}")
    assert r.status_code == 200, r.text
    assert not (lib / "Job Uploads" / "a.stl").exists()


@pytest.mark.asyncio
async def test_delete_blocked_by_project_item_reference_and_file_survives(client, lib, session_factory):
    # A file referenced by a ProjectItem is RESTRICT-protected at the DB level.
    # The route must reject with 409 up front, and must NOT delete the file off
    # disk first (that would strand the ProjectItem pointing at nothing).
    up = (await client.post("/api/v1/files/upload", files=_stl("a.stl"))).json()
    from app.models import Project, ProjectItem
    async with session_factory() as session:
        project = Project(name="P", order_type="internal", created_at="t", updated_at="t")
        session.add(project)
        await session.flush()
        session.add(ProjectItem(project_id=project.id, file_id=up["id"], quantity=1))
        await session.commit()

    r = await client.delete(f"/api/v1/files/{up['id']}")
    assert r.status_code == 409
    assert (lib / "Job Uploads" / "a.stl").is_file()


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


# ---------------------------------------------------------------------------
# POST /files/rescan — the route around LibraryScanner
# ---------------------------------------------------------------------------

async def test_rescan_indexes_files_dropped_on_disk_and_forgets_deleted_ones(client, lib):
    (lib / "Customers").mkdir()
    dropped = lib / "Customers" / "dropped.stl"
    dropped.write_bytes(b"solid dropped\nendsolid dropped\n")

    first = await client.post("/api/v1/files/rescan")
    listed = (await client.get("/api/v1/files")).json()
    second = await client.post("/api/v1/files/rescan")

    assert (first.status_code, first.json()) == (200, {"added": 1, "moved": 0, "removed": 0, "missing": 0})
    assert [(f["original_filename"], f["folder"], f["missing"]) for f in listed] == [("dropped.stl", "/Customers", False)]
    assert second.json() == {"added": 0, "moved": 0, "removed": 0, "missing": 0}  # idempotent

    dropped.unlink()
    third = await client.post("/api/v1/files/rescan")
    assert third.json() == {"added": 0, "moved": 0, "removed": 1, "missing": 0}
    assert (await client.get("/api/v1/files")).json() == []


async def test_rescan_flags_a_vanished_file_that_a_job_still_references_instead_of_deleting_it(client, lib, create_job, upload_3mf):
    file_id = await upload_3mf(filename="used.3mf")
    job_id = await create_job(file_id=file_id)
    next(lib.rglob("used.3mf")).unlink()

    resp = await client.post("/api/v1/files/rescan")

    assert resp.json() == {"added": 0, "moved": 0, "removed": 0, "missing": 1}
    (row,) = (await client.get("/api/v1/files")).json()
    assert (row["id"], row["missing"]) == (file_id, True)
    assert (await client.get(f"/api/v1/jobs/{job_id}")).json()["uploaded_file_id"] == file_id  # job untouched


# ---------------------------------------------------------------------------
# Tag assignment / removal on a file
# ---------------------------------------------------------------------------

async def _tag(client, name="PLA", **body):
    resp = await client.post("/api/v1/tags", json={"name": name, "color": "#22c55e", "category": "Material", **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _tagged_names(client, **params) -> list[str]:
    return [f["original_filename"] for f in (await client.get("/api/v1/files", params=params)).json()]


async def test_removing_a_tag_from_a_file_stops_it_matching_the_filter_and_frees_the_usage_count(client, lib):
    a = (await client.post("/api/v1/files/upload", files=_stl("a.stl"))).json()
    b = (await client.post("/api/v1/files/upload", files=_stl_with("b.stl", b"solid b\nendsolid b\n"))).json()
    tag = await _tag(client)
    for f in (a, b):
        assert (await client.post(f"/api/v1/files/{f['id']}/tags", json={"tag_id": tag["id"]})).status_code == 200
    assert sorted(await _tagged_names(client, tags=["PLA"])) == ["a.stl", "b.stl"]
    assert (await client.get("/api/v1/tags")).json()[0]["usage_count"] == 2

    resp = await client.delete(f"/api/v1/files/{a['id']}/tags/{tag['id']}")

    assert (resp.status_code, resp.json()) == (200, {"file_id": a["id"], "tag_id": tag["id"]})
    assert await _tagged_names(client, tags=["PLA"]) == ["b.stl"]
    assert (await client.get("/api/v1/tags")).json()[0]["usage_count"] == 1
    listed = {f["original_filename"]: f["tags"] for f in (await client.get("/api/v1/files")).json()}
    assert listed["a.stl"] == [] and [t["name"] for t in listed["b.stl"]] == ["PLA"]


async def test_removing_or_assigning_a_tag_is_idempotent_and_assign_validates_its_targets(client, lib):
    f = (await client.post("/api/v1/files/upload", files=_stl("a.stl"))).json()
    tag = await _tag(client)

    twice_removed = [await client.delete(f"/api/v1/files/{f['id']}/tags/{tag['id']}") for _ in range(2)]
    twice_assigned = [await client.post(f"/api/v1/files/{f['id']}/tags", json={"tag_id": tag["id"]}) for _ in range(2)]
    unknown_tag = await client.post(f"/api/v1/files/{f['id']}/tags", json={"tag_id": 999})
    unknown_file = await client.post(f"/api/v1/files/999/tags", json={"tag_id": tag["id"]})

    assert [r.status_code for r in twice_removed + twice_assigned] == [200] * 4
    assert (unknown_tag.status_code, unknown_file.status_code) == (404, 404)
    assert (await client.get("/api/v1/tags")).json()[0]["usage_count"] == 1  # assigned twice, counted once


# ---------------------------------------------------------------------------
# GET /files/{id}/model-filaments and /embedded-settings on a real project 3MF
# ---------------------------------------------------------------------------

def _project_3mf(settings: dict) -> bytes:
    import json, zipfile
    from tests.conftest import make_3mf_bytes
    buf = io.BytesIO(make_3mf_bytes())
    with zipfile.ZipFile(buf, "a") as zf:
        zf.writestr(zipfile.ZipInfo("Metadata/project_settings.config", date_time=(2026, 1, 1, 0, 0, 0)), json.dumps(settings))
    return buf.getvalue()


async def test_model_filaments_lists_what_the_3mf_declares_with_one_based_indexes(client, lib, upload_3mf):
    file_id = await upload_3mf(data=_project_3mf({"filament_colour": ["#FF0000", "#00FF00", "#0000FF"],
                                                  "filament_type": ["PLA", "PETG"]}))

    resp = await client.get(f"/api/v1/files/{file_id}/model-filaments")

    assert (resp.status_code, resp.json()) == (200, [
        {"index": 1, "color": "#FF0000", "type": "PLA"},
        {"index": 2, "color": "#00FF00", "type": "PETG"},
        {"index": 3, "color": "#0000FF", "type": ""},   # more colours than types: type left blank
    ])


async def test_model_filaments_is_empty_for_a_3mf_without_project_settings_and_404_for_unknown_files(client, lib, upload_3mf):
    bare = await upload_3mf()  # factory 3MF: plates only, no project_settings.config

    assert (await client.get(f"/api/v1/files/{bare}/model-filaments")).json() == []
    missing = await client.get("/api/v1/files/999/model-filaments")
    assert (missing.status_code, missing.json()["detail"]) == (404, "File 999 not found")


async def test_embedded_settings_returns_only_curated_keys_with_labels(client, lib, upload_3mf):
    file_id = await upload_3mf(data=_project_3mf({"layer_height": "0.2", "wall_loops": ["3"], "fan_speed": "99"}))

    resp = await client.get(f"/api/v1/files/{file_id}/embedded-settings")

    assert resp.status_code == 200
    assert resp.json() == [{"key": "wall_loops", "label": "Wall loops", "value": "3"},
                           {"key": "layer_height", "label": "Layer height", "value": "0.2"}]
    assert (await client.get("/api/v1/files/999/embedded-settings")).status_code == 404

