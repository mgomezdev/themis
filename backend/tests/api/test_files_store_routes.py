"""Library CRUD routes on the ArtifactStore (BIZ-285): characterization of naming/dedup plus the decisions settled at
the rewire (docs/architecture/artifact-storage.md section 5a). Rollback of a failed version move lives in
test_library_cached_versions.py."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from tests.conftest import make_3mf_bytes

UP = "Job Uploads"


@pytest.fixture
def dirs(tmp_path):
    lib, cache = tmp_path / "library", tmp_path / "filecache"
    lib.mkdir()
    cache.mkdir()
    with patch("app.config.get_library_dir", return_value=lib), patch("app.config.get_filecache_dir", return_value=cache):
        yield lib, cache


async def _upload(client, name="m.3mf", data=None, extra=None):
    return await client.post("/api/v1/files/upload", data=extra,
                             files={"file": (name, data if data is not None else make_3mf_bytes(), "application/octet-stream")})


def _tree(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


async def test_uploads_get_umask_permissions_not_0600(client, dirs):
    lib, _ = dirs
    old = os.umask(0o022)
    try:
        assert (await _upload(client)).status_code == 201
    finally:
        os.umask(old)
    assert (lib / UP / "m.3mf").stat().st_mode & 0o777 == 0o644


async def test_upload_leaves_no_scratch_file_in_any_outcome(client, dirs):
    lib, _ = dirs
    await _upload(client)
    await _upload(client)                                   # dedup hit
    await _upload(client, data=make_3mf_bytes() + b"x")    # same name, new content -> "m (2).3mf"
    assert _tree(lib) == [f"{UP}/m (2).3mf", f"{UP}/m.3mf"]


async def test_upload_of_a_near_limit_name_round_trips_end_to_end(client, dirs):
    lib, _ = dirs
    name = "a" * 245 + ".stl"   # 249 bytes. End-to-end characterization only: the route's scratch+move never needed the
    # temp-name cap; that is pinned at store level (test_a_key_near_the_255_byte_limit_can_be_written).
    resp = await _upload(client, name=name, data=b"solid x")
    assert resp.status_code == 201 and (lib / UP / name).read_bytes() == b"solid x"


async def test_upload_restores_an_orphaned_record_from_the_new_bytes(client, dirs):
    lib, _ = dirs
    data = make_3mf_bytes()
    first = (await _upload(client, data=data)).json()
    (lib / UP / "m.3mf").unlink()
    again = (await _upload(client, data=data)).json()
    assert again["id"] == first["id"] and (lib / UP / "m.3mf").read_bytes() == data and _tree(lib) == [f"{UP}/m.3mf"]


async def test_rejected_eligibility_leaves_nothing_behind(client, dirs):
    lib, _ = dirs
    resp = await _upload(client, name="x.gcode", data=b"G28\n", extra={"eligible_model_uuids": ["no-such-model"]})
    assert resp.status_code == 422 and _tree(lib) == []
    assert (await client.get("/api/v1/files")).json() == []


async def test_moving_or_deleting_the_last_file_keeps_the_folder(client, dirs):
    lib, _ = dirs
    fid = (await _upload(client)).json()["id"]
    assert (await client.patch(f"/api/v1/files/{fid}", json={"folder": "/Archive"})).status_code == 200
    assert (lib / UP).is_dir() and (lib / "Archive" / "m.3mf").is_file()
    assert (await client.delete(f"/api/v1/files/{fid}")).status_code == 200
    assert (lib / "Archive").is_dir()


async def test_rename_onto_an_existing_name_gets_a_suffix(client, dirs):
    lib, _ = dirs
    await _upload(client, name="a.3mf")
    b = (await _upload(client, name="b.3mf", data=make_3mf_bytes() + b"b")).json()["id"]
    body = (await client.patch(f"/api/v1/files/{b}", json={"name": "a.3mf"})).json()
    assert body["original_filename"] == "a (2).3mf" and body["relative_path"] == f"{UP}/a (2).3mf"
    assert _tree(lib) == [f"{UP}/a (2).3mf", f"{UP}/a.3mf"]


async def test_rename_to_a_part_name_is_refused(client, dirs):
    lib, _ = dirs
    fid = (await _upload(client)).json()["id"]
    resp = await client.patch(f"/api/v1/files/{fid}", json={"name": "m.3mf.part"})
    assert resp.status_code == 400
    assert _tree(lib) == [f"{UP}/m.3mf"]
    assert (await client.get("/api/v1/files")).json()[0]["original_filename"] == "m.3mf"


async def test_rename_of_a_file_missing_on_disk_only_updates_the_row(client, dirs):
    lib, _ = dirs
    fid = (await _upload(client)).json()["id"]
    (lib / UP / "m.3mf").unlink()
    body = (await client.patch(f"/api/v1/files/{fid}", json={"name": "n.3mf"})).json()
    assert body["relative_path"] == f"{UP}/n.3mf" and _tree(lib) == []


async def test_download_streams_the_bytes_with_the_original_filename(client, dirs):
    data = make_3mf_bytes()
    fid = (await _upload(client, name="Müller part.3mf", data=data)).json()["id"]
    resp = await client.get(f"/api/v1/files/{fid}/download")
    assert resp.status_code == 200 and resp.content == data
    assert resp.headers["content-length"] == str(len(data))
    assert "attachment" in resp.headers["content-disposition"]
    assert "M%C3%BCller%20part.3mf" in resp.headers["content-disposition"]


async def test_download_of_a_file_gone_from_disk_is_404(client, dirs):
    lib, _ = dirs
    fid = (await _upload(client)).json()["id"]
    (lib / UP / "m.3mf").unlink()
    assert (await client.get(f"/api/v1/files/{fid}/download")).status_code == 404


async def test_thumbnail_is_served_as_png_and_traversal_is_refused(client, dirs):
    _, cache = dirs
    fid = (await _upload(client)).json()["id"]
    (cache / str(fid) / "thumbnails").mkdir(parents=True, exist_ok=True)
    (cache / str(fid) / "thumbnails" / "plate_9.png").write_bytes(b"\x89PNGdata")
    ok = await client.get(f"/api/v1/files/{fid}/thumbnails/plate_9.png")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png" and ok.content == b"\x89PNGdata"
    assert (await client.get(f"/api/v1/files/{fid}/thumbnails/nope.png")).status_code == 404


async def test_thumbnail_filename_traversal_is_refused(client, dirs, session_factory):
    from fastapi import HTTPException
    from app.api.routes.files import get_thumbnail
    fid = (await _upload(client)).json()["id"]
    async with session_factory() as s:   # httpx normalises ".." out of URLs, so call the handler like the router would
        for bad in ("..", "../../x.png", "a/../../b.png"):
            with pytest.raises(HTTPException) as exc:
                await get_thumbnail(fid, bad, s)
            assert exc.value.status_code == 400


async def test_delete_removes_bytes_and_thumbnails(client, dirs):
    lib, cache = dirs
    fid = (await _upload(client)).json()["id"]
    assert (cache / str(fid)).exists()
    assert (await client.delete(f"/api/v1/files/{fid}")).status_code == 200
    assert _tree(lib) == [] and not (cache / str(fid)).exists()


async def test_thumbnail_name_containing_dots_inside_a_segment_is_served(client, dirs):
    _, cache = dirs
    fid = (await _upload(client)).json()["id"]
    (cache / str(fid) / "thumbnails").mkdir(parents=True, exist_ok=True)
    (cache / str(fid) / "thumbnails" / "a..b.png").write_bytes(b"png")
    resp = await client.get(f"/api/v1/files/{fid}/thumbnails/a..b.png")
    assert resp.status_code == 200 and resp.content == b"png"


async def test_delete_survives_a_stale_row_with_an_unremovable_key(client, dirs, session_factory):
    from app.models import SlicedVersion, UploadedFile
    lib, _ = dirs
    model = (await _upload(client, name="model.3mf")).json()["id"]
    version = (await _upload(client, name="v.gcode", data=b"G28\n")).json()["id"]
    outside = lib.parent / "outside"
    outside.mkdir()
    (lib / "link").symlink_to(outside)           # a symlink leaving the library root
    async with session_factory() as s:
        (await s.get(UploadedFile, model)).relative_path = "link/x.3mf"   # stale row: its key escapes the store
        s.add(SlicedVersion(file_id=version, source_file_id=model, plate_number=1, machine_preset="m",
                            process_preset="p", cache_key="k", artifact_kind="gcode", created_at="2026-01-01"))
        await s.commit()
    assert (await client.get(f"/api/v1/files/{model}/download")).status_code == 400
    # the model (first in line) cannot be removed; the version's file behind it still must be
    resp = await client.delete(f"/api/v1/files/{model}?versions=delete")
    assert resp.status_code == 200, resp.text
    assert not (lib / UP / "v.gcode").exists()
    assert not any(r["id"] in (model, version) for r in (await client.get("/api/v1/files")).json())
