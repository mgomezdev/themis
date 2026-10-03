"""The library and its cached sliced versions (BIZ-195, BIZ-196): delete/move/rescan keep the model ↔ version link
honest, the file list filters by kind, and flows that slice refuse pre-sliced files."""
import base64
import logging
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import Job, Project, SlicedVersion, UploadedFile
from tests.api.test_slice_cache_api import _version_for, library  # noqa: F401 — `library` is a fixture
from tests.conftest import make_sliced_archive

UP = "Job Uploads"   # where API uploads land


async def _file(session_factory, file_id) -> UploadedFile | None:
    async with session_factory() as s:
        return await s.get(UploadedFile, file_id)


async def _version(session_factory, version_id) -> SlicedVersion | None:
    async with session_factory() as s:
        return await s.get(SlicedVersion, version_id)


# ---- delete (BIZ-195) ----------------------------------------------------------------------------------------------

async def test_deleting_a_model_with_versions_asks_first(client, library, upload_3mf, session_factory):
    model = await upload_3mf()
    v1, f1 = await _version_for(session_factory, library, model, name="a.gcode.3mf")

    resp = await client.delete(f"/api/v1/files/{model}")

    assert resp.status_code == 409
    assert resp.json()["detail"]["versions"] == [
        {"id": v1, "file_id": f1, "name": "a.gcode.3mf", "folder": "/Job Uploads"}]
    assert await _file(session_factory, model) is not None and (library / UP / "m.3mf").exists()


async def test_delete_together_removes_the_model_and_its_versions(client, library, upload_3mf, session_factory):
    model = await upload_3mf()
    v1, f1 = await _version_for(session_factory, library, model, name="a.gcode.3mf")
    v2, f2 = await _version_for(session_factory, library, model, name="b.gcode.3mf")

    resp = await client.delete(f"/api/v1/files/{model}?versions=delete")

    assert resp.status_code == 200 and sorted(resp.json()["deleted_versions"]) == sorted([f1, f2])
    for fid in (model, f1, f2):
        assert await _file(session_factory, fid) is None
    assert await _version(session_factory, v1) is None and await _version(session_factory, v2) is None
    assert sorted(p.name for p in (library / UP).iterdir()) == []


async def test_keep_leaves_the_versions_as_standalone_gcode(client, library, upload_3mf, session_factory):
    model = await upload_3mf()
    v1, f1 = await _version_for(session_factory, library, model, name="a.gcode.3mf")

    resp = await client.delete(f"/api/v1/files/{model}?versions=keep")

    assert resp.status_code == 200 and resp.json()["deleted_versions"] == []
    assert await _file(session_factory, model) is None and not (library / UP / "m.3mf").exists()
    assert (await _version(session_factory, v1)).source_file_id is None
    assert (library / UP / "a.gcode.3mf").exists()
    listed = next(f for f in (await client.get("/api/v1/files")).json() if f["id"] == f1)
    assert listed["sliced_version"]["source_file_id"] is None


async def test_delete_together_refuses_when_a_version_is_in_use_and_deletes_nothing(
        client, library, upload_3mf, create_printer, session_factory):
    model = await upload_3mf()
    _, f1 = await _version_for(session_factory, library, model, name="a.gcode.3mf")
    _, f2 = await _version_for(session_factory, library, model, name="b.gcode.3mf")
    async with session_factory() as s:
        s.add(Job(uploaded_file_id=f2, plate_number=1, status="printing", created_at="t", updated_at="t"))
        await s.commit()

    resp = await client.delete(f"/api/v1/files/{model}?versions=delete")

    assert resp.status_code == 409 and "b.gcode.3mf" in resp.json()["detail"]
    for fid in (model, f1, f2):
        assert await _file(session_factory, fid) is not None
    assert (library / UP / "m.3mf").exists() and (library / UP / "a.gcode.3mf").exists()


async def test_deleting_a_cached_file_drops_its_version(client, library, upload_3mf, session_factory):
    model = await upload_3mf()
    v1, f1 = await _version_for(session_factory, library, model, name="a.gcode.3mf")

    assert (await client.delete(f"/api/v1/files/{f1}")).status_code == 200

    assert await _version(session_factory, v1) is None and await _file(session_factory, model) is not None


async def test_a_model_without_versions_deletes_as_before(client, library, upload_3mf, session_factory):
    model = await upload_3mf()
    assert (await client.delete(f"/api/v1/files/{model}")).status_code == 200
    assert await _file(session_factory, model) is None


# ---- move / rename (BIZ-195) ---------------------------------------------------------------------------------------

async def test_moving_a_model_moves_its_versions_with_it(client, library, upload_3mf, session_factory):
    model = await upload_3mf()
    _, f1 = await _version_for(session_factory, library, model, name="a.gcode.3mf")
    (library / "Archive").mkdir()
    (library / "Archive" / "a.gcode.3mf").write_bytes(b"already here")

    with patch("app.config.get_library_dir", return_value=library):
        resp = await client.patch(f"/api/v1/files/{model}", json={"folder": "/Archive"})

    assert resp.status_code == 200, resp.text
    moved = await _file(session_factory, f1)
    assert (moved.relative_path, moved.folder) == ("Archive/a (2).gcode.3mf", "/Archive")
    assert (library / "Archive" / "a (2).gcode.3mf").exists() and not (library / UP / "a.gcode.3mf").exists()
    assert (library / "Archive" / "a.gcode.3mf").read_bytes() == b"already here"


async def test_renaming_a_model_leaves_its_versions_alone(client, library, upload_3mf, session_factory):
    model = await upload_3mf()
    _, f1 = await _version_for(session_factory, library, model, name="a.gcode.3mf")

    with patch("app.config.get_library_dir", return_value=library):
        resp = await client.patch(f"/api/v1/files/{model}", json={"name": "renamed.3mf"})

    assert resp.status_code == 200
    assert (await _file(session_factory, f1)).relative_path == f"{UP}/a.gcode.3mf"


async def test_renaming_a_cached_file_keeps_its_link(client, library, upload_3mf, session_factory):
    model = await upload_3mf()
    v1, f1 = await _version_for(session_factory, library, model, name="a.gcode.3mf")

    with patch("app.config.get_library_dir", return_value=library):
        await client.patch(f"/api/v1/files/{f1}", json={"name": "Benchy PETG.gcode.3mf", "folder": "/Elsewhere"})

    assert (await _version(session_factory, v1)).source_file_id == model
    versions = (await client.get(f"/api/v1/files/{model}/sliced-versions")).json()
    assert [v["name"] for v in versions] == ["Benchy PETG.gcode.3mf"]


# ---- the filesystem is the source of truth (BIZ-195) --------------------------------------------------------------

async def _rescan(client, library, tmp_path):
    with patch("app.config.get_library_dir", return_value=library), \
         patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"):
        assert (await client.post("/api/v1/files/rescan")).status_code == 200


async def test_a_cached_file_moved_by_hand_keeps_its_version(client, library, upload_3mf, session_factory, tmp_path):
    model = await upload_3mf()
    v1, f1 = await _version_for(session_factory, library, model, name="a.gcode.3mf")
    async with session_factory() as s:   # the scanner tracks moves by content hash
        f = await s.get(UploadedFile, f1)
        f.content_hash = __import__("hashlib").sha256((library / UP / "a.gcode.3mf").read_bytes()).hexdigest()
        await s.commit()
    (library / "Moved").mkdir()
    (library / UP / "a.gcode.3mf").rename(library / "Moved" / "a.gcode.3mf")

    await _rescan(client, library, tmp_path)

    assert (await _file(session_factory, f1)).relative_path == "Moved/a.gcode.3mf"
    assert (await _version(session_factory, v1)).source_file_id == model


async def test_a_cached_file_deleted_by_hand_goes_missing_and_comes_back(
        client, library, upload_3mf, session_factory, tmp_path):
    model = await upload_3mf()
    _, f1 = await _version_for(session_factory, library, model, name="a.gcode.3mf")
    async with session_factory() as s:   # a job printed it, so the row is kept as missing
        s.add(Job(uploaded_file_id=f1, plate_number=1, status="complete", created_at="t", updated_at="t"))
        f = await s.get(UploadedFile, f1)
        f.content_hash = __import__("hashlib").sha256((library / UP / "a.gcode.3mf").read_bytes()).hexdigest()
        await s.commit()
    data = (library / UP / "a.gcode.3mf").read_bytes()
    (library / UP / "a.gcode.3mf").unlink()

    await _rescan(client, library, tmp_path)
    assert (await client.get(f"/api/v1/files/{model}/sliced-versions")).json() == []

    (library / UP / "a.gcode.3mf").write_bytes(data)
    await _rescan(client, library, tmp_path)
    assert [v["file_id"] for v in (await client.get(f"/api/v1/files/{model}/sliced-versions")).json()] == [f1]


async def test_a_cached_file_edited_by_hand_is_detached_from_its_model(
        client, library, upload_3mf, session_factory, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="app.services.slice_cache")
    model = await upload_3mf()
    v1, f1 = await _version_for(session_factory, library, model, name="a.gcode.3mf")
    (library / UP / "a.gcode.3mf").write_bytes(make_sliced_archive(((1, 9.9, 99),)))   # different bytes, same path

    await _rescan(client, library, tmp_path)

    assert (await _version(session_factory, v1)).source_file_id is None
    assert any("event=version_detached" in r.getMessage() and "reason=file_edited" in r.getMessage()
               for r in caplog.records)


# ---- listing (BIZ-196) ---------------------------------------------------------------------------------------------

async def test_the_file_list_filters_by_kind(client, library, upload_3mf):
    stl = await upload_3mf("part.stl", b"solid x\nendsolid x\n")
    tmf = await upload_3mf()
    gcode = await upload_3mf("part.gcode", b"; filament used [g] = 1\nG28\n")
    archive = await upload_3mf("part.gcode.3mf", make_sliced_archive())

    async def ids(kind):
        return sorted(f["id"] for f in (await client.get(f"/api/v1/files?kind={kind}")).json())

    assert await ids("models") == sorted([stl, tmf])
    assert await ids("sliced") == sorted([gcode, archive])
    assert await ids("all") == sorted([stl, tmf, gcode, archive])
    assert (await client.get("/api/v1/files?kind=bogus")).status_code == 422


_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 40


def _gcode_with_thumbnails() -> bytes:
    def block(w, h, png):
        b64 = base64.b64encode(png).decode()
        lines = [b64[i:i + 76] for i in range(0, len(b64), 76)]
        return (f"; thumbnail begin {w}x{h} {len(b64)}\n" + "".join(f"; {ln}\n" for ln in lines) +
                "; thumbnail end\n").encode()
    return (b"; HEADER_BLOCK_START\n" + block(16, 16, b"\x89PNG small") + block(300, 300, _PNG) +
            b"; filament used [g] = 1\nG28\n")


async def test_a_gcode_with_an_embedded_preview_gets_a_thumbnail(client, library, upload_3mf, tmp_path):
    file_id = await upload_3mf("part.gcode", _gcode_with_thumbnails())

    listed = next(f for f in (await client.get("/api/v1/files")).json() if f["id"] == file_id)

    assert listed["thumbnail_url"] == f"/api/v1/files/{file_id}/thumbnails/plate_1.png"
    assert (tmp_path / "filecache" / str(file_id) / "thumbnails" / "plate_1.png").read_bytes() == _PNG   # the largest


async def test_a_cached_gcode_without_a_preview_borrows_its_models_thumbnail(
        client, library, upload_3mf, session_factory):
    model = await upload_3mf()   # make_3mf_bytes carries a plate_1.png
    _, cached = await _version_for(session_factory, library, model, name="a.gcode", on_disk=False)

    files = {f["id"]: f for f in (await client.get("/api/v1/files")).json()}

    assert files[cached]["thumbnail_url"] == files[model]["thumbnail_url"] is not None


async def test_reuploading_a_cached_files_bytes_returns_that_file(client, library, upload_3mf, session_factory):
    """Upload dedup is per folder by content hash: identical bytes in the same folder are the same file — intended."""
    first = await upload_3mf("a.gcode.3mf", make_sliced_archive())
    again = await upload_3mf("other-name.gcode.3mf", make_sliced_archive())
    assert again == first


# ---- sliceable-only flows (BIZ-196) ------------------------------------------------------------------------------

@pytest.mark.parametrize("name,data", [
    ("part.gcode", b"; filament used [g] = 1\nG28\n"),
    ("part.gcode.3mf", make_sliced_archive()),
])
async def test_a_pre_sliced_file_cannot_be_a_project_item(client, library, upload_3mf, session_factory, name, data):
    project = (await client.post("/api/v1/projects", json={"name": "P"})).json()["id"]
    file_id = await upload_3mf(name, data)

    resp = await client.post(f"/api/v1/projects/{project}/items", json={"file_id": file_id, "quantity": 1})

    assert resp.status_code == 422
    async with session_factory() as s:
        assert (await s.get(Project, project)) is not None
        assert (await client.get(f"/api/v1/projects/{project}/items")).json() == []
