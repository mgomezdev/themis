"""Library scan, content-hash change detection, path-bound parsers and thumbnail keys on the ArtifactStore (BIZ-286).

The scanner runs against `InMemoryArtifactStore` (a stand-in for a remote backend: no local paths, `materialize` copies to a temp
dir) as well as local disk, so a scan that quietly reached for `library_dir` would fail here. Design: docs/architecture/artifact-storage.md.
"""
from __future__ import annotations

import dataclasses
import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import UploadedFile
from app.services.artifact_store import ArtifactInfo, LocalDiskArtifactStore
from app.services.library_scanner import LibraryScanner, content_changed, fresh_content_hash, refresh_content_hash
from app.services.library_store import with_local_path
from tests.conftest import make_3mf_bytes, slice_info_xml
from tests.fake_artifact_store import InMemoryArtifactStore

PNG = b"\x89PNG\r\n\x1a\n"


def _scanner(session, lib_store, cache_store, tmp_path):
    # library_dir / filecache_dir point at directories that do not exist: only the injected stores may be used.
    return LibraryScanner(session, tmp_path / "no-lib", tmp_path / "no-cache", library_store=lib_store, filecache_store=cache_store)


def _3mf(png: bytes = PNG + b"1") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("Metadata/slice_info.config", date_time=(2026, 1, 1, 0, 0, 0)), slice_info_xml([(1, 60, 5.0)]))
        zf.writestr(zipfile.ZipInfo("Metadata/plate_1.png", date_time=(2026, 1, 1, 0, 0, 0)), png)
    return buf.getvalue()


# ---- change token ---------------------------------------------------------------------------------------------

ROW = ("h1", 10, 100.0)


def test_local_change_detection_is_size_and_mtime():
    assert not content_changed(ArtifactInfo("k", 10, 100.0), *ROW)
    assert content_changed(ArtifactInfo("k", 11, 100.0), *ROW)
    assert content_changed(ArtifactInfo("k", 10, 101.0), *ROW)


def test_a_backend_supplied_hash_replaces_size_and_mtime():
    assert not content_changed(ArtifactInfo("k", 99, 999.0, sha256="h1"), *ROW)   # touched/re-uploaded, same bytes
    assert content_changed(ArtifactInfo("k", 10, 100.0, sha256="h2"), *ROW)       # same size+mtime, different bytes


def test_an_unhashed_row_always_counts_as_changed():
    assert content_changed(ArtifactInfo("k", 10, 100.0), "", 10, 100.0)


class _CountingStore(LocalDiskArtifactStore):
    hashed = 0

    def sha256(self, key):
        _CountingStore.hashed += 1
        return super().sha256(key)


def test_fresh_content_hash_rehashes_only_when_the_change_token_moved(tmp_path):
    store = _CountingStore(tmp_path)
    info = store.put("a.stl", io.BytesIO(b"abc"))
    _CountingStore.hashed = 0
    assert fresh_content_hash(store, "a.stl", info.sha256, info.size, info.mtime) == (info.sha256, info.size, info.mtime)
    assert _CountingStore.hashed == 0
    again = fresh_content_hash(store, "a.stl", "stale", info.size, info.mtime - 5)   # token moved
    assert again == (info.sha256, info.size, info.mtime) and _CountingStore.hashed == 1
    store.put("a.stl", io.BytesIO(b"abcd"), overwrite=True)
    new = fresh_content_hash(store, "a.stl", info.sha256, info.size, info.mtime)
    assert new[0] != info.sha256 and new[1] == 4


def test_fresh_content_hash_is_none_for_a_gone_or_unusable_key(tmp_path):
    store = LocalDiskArtifactStore(tmp_path)
    assert fresh_content_hash(store, "nope.stl", "h", 1, 1.0) is None
    assert fresh_content_hash(store, "../x.stl", "h", 1, 1.0) is None


async def test_refresh_content_hash_updates_the_row_through_the_store(tmp_path):
    store = LocalDiskArtifactStore(tmp_path)
    store.put("a.stl", io.BytesIO(b"v1"))
    row = UploadedFile(relative_path="a.stl", content_hash="old", size_bytes=1, mtime=1.0)
    assert await refresh_content_hash(row, store) is True
    assert (row.content_hash, row.size_bytes) == (store.sha256("a.stl"), 2)
    assert await refresh_content_hash(row, store) is False
    gone = UploadedFile(relative_path="gone.stl", content_hash="old", size_bytes=1, mtime=1.0)
    assert await refresh_content_hash(gone, store) is False and gone.content_hash == "old"


# ---- scan on a remote-like store ------------------------------------------------------------------------------

async def test_scan_indexes_parses_and_publishes_thumbnails_without_any_local_library(tmp_path, session_factory):
    lib, cache = InMemoryArtifactStore(), InMemoryArtifactStore()
    lib.put("Customers/Vela/m.3mf", io.BytesIO(_3mf()))
    lib.put("Customers/Vela/arm.stl", io.BytesIO(b"solid"))
    lib.put("notes.txt", io.BytesIO(b"ignored"))
    async with session_factory() as s:
        summary = await _scanner(s, lib, cache, tmp_path).scan()
        rows = {r.relative_path: r for r in (await s.execute(select(UploadedFile))).scalars()}
    assert summary["added"] == 2 and set(rows) == {"Customers/Vela/m.3mf", "Customers/Vela/arm.stl"}
    m = rows["Customers/Vela/m.3mf"]
    assert m.folder == "/Customers/Vela" and m.content_hash == lib.sha256(m.relative_path)
    (plate,) = m.plates
    assert plate["thumbnail_path"] == f"{m.id}/thumbnails/plate_1.png"          # a filecache KEY, not a path
    assert (plate["estimated_time"], plate["filament_g"]) == (60, 5.0)
    with cache.open(plate["thumbnail_path"]) as fh:
        assert fh.read() == PNG + b"1"
    assert rows["Customers/Vela/arm.stl"].plates == [
        {"plate_number": 1, "thumbnail_path": None, "estimated_time": 0, "filament_g": 0.0}]


async def test_rescan_reparses_an_edited_file_but_not_an_unchanged_one(tmp_path, session_factory):
    lib, cache = InMemoryArtifactStore(), InMemoryArtifactStore()
    lib.put("m.3mf", io.BytesIO(_3mf(PNG + b"1")))
    async with session_factory() as s:
        sc = _scanner(s, lib, cache, tmp_path)
        await sc.scan()
        with patch.object(LibraryScanner, "parse_plates", side_effect=AssertionError("reparsed")):
            assert (await sc.scan())["added"] == 0                       # unchanged: no hash, no parse
        lib.put("m.3mf", io.BytesIO(_3mf(PNG + b"2")), overwrite=True)
        await sc.scan()
        row = (await s.execute(select(UploadedFile))).scalars().one()
    assert row.content_hash == lib.sha256("m.3mf")
    with cache.open(row.plates[0]["thumbnail_path"]) as fh:
        assert fh.read() == PNG + b"2"


class _HashedListing(InMemoryArtifactStore):
    """A backend whose listing carries a content hash (like an S3 checksum), and whose mtime changes on every touch."""
    def list(self, prefix=""):
        return iter([dataclasses.replace(i, sha256=self.sha256(i.key)) for i in super().list(prefix)])


async def test_a_backend_hash_means_a_touched_but_identical_file_is_not_reparsed(tmp_path, session_factory):
    lib, cache = _HashedListing(), InMemoryArtifactStore()
    lib.put("m.3mf", io.BytesIO(_3mf()))
    async with session_factory() as s:
        sc = _scanner(s, lib, cache, tmp_path)
        await sc.scan()
        lib.put("m.3mf", io.BytesIO(_3mf()), overwrite=True)            # new mtime, same bytes
        row = (await s.execute(select(UploadedFile))).scalars().one()
        row.mtime = 1.0                                                    # as if the indexed mtime were old
        with patch.object(LibraryScanner, "parse_plates", side_effect=AssertionError("reparsed")):
            await sc.scan()


async def test_a_moved_file_is_recognised_by_hash_through_the_store(tmp_path, session_factory):
    lib, cache = InMemoryArtifactStore(), InMemoryArtifactStore()
    lib.put("old/p.stl", io.BytesIO(b"unique-bytes"))
    async with session_factory() as s:
        sc = _scanner(s, lib, cache, tmp_path)
        await sc.scan()
        lib.move("old/p.stl", "new/p.stl")
        summary = await sc.scan()
        row = (await s.execute(select(UploadedFile))).scalars().one()
    assert (summary["moved"], summary["added"], summary["removed"]) == (1, 0, 0)
    assert (row.relative_path, row.folder) == ("new/p.stl", "/new")


async def test_a_copy_with_the_same_bytes_is_a_new_file_not_a_move(tmp_path, session_factory):
    lib, cache = InMemoryArtifactStore(), InMemoryArtifactStore()
    lib.put("z/p.stl", io.BytesIO(b"same-bytes"))
    async with session_factory() as s:
        sc = _scanner(s, lib, cache, tmp_path)
        await sc.scan()
        lib.put("a/p.stl", io.BytesIO(b"same-bytes"))                    # the original (sorts later) is still there
        summary = await sc.scan()
        paths = sorted(r.relative_path for r in (await s.execute(select(UploadedFile))).scalars())
    assert (summary["moved"], summary["added"]) == (0, 1) and paths == ["a/p.stl", "z/p.stl"]


async def test_a_row_with_no_hash_is_rehashed_even_when_size_and_mtime_match(tmp_path, session_factory):
    lib, cache = InMemoryArtifactStore(), InMemoryArtifactStore()
    info = lib.put("a.stl", io.BytesIO(b"abc"))
    async with session_factory() as s:
        s.add(UploadedFile(original_filename="a.stl", relative_path="a.stl", folder="/", size_bytes=info.size,
                           content_hash="", mtime=info.mtime, plates=[], missing=False, uploaded_at="2026-01-01"))
        await s.commit()
        summary = await _scanner(s, lib, cache, tmp_path).scan()
        row = (await s.execute(select(UploadedFile))).scalars().one()
    assert summary["added"] == 0 and row.content_hash == info.sha256


async def test_a_row_whose_file_is_readable_but_not_listable_is_kept_not_deleted_or_flagged(tmp_path, session_factory):
    lib_dir, cache_dir, outside = tmp_path / "lib", tmp_path / "cache", tmp_path / "nas.stl"
    (lib_dir / "Models").mkdir(parents=True)
    outside.write_bytes(b"on the nas")
    (lib_dir / "Models" / "foo.stl").symlink_to(outside)            # escapes the root: the store refuses and does not list it
    (lib_dir / "back\\slash.stl").write_bytes(b"x")                 # not a valid key
    (lib_dir / "gone.stl").write_bytes(b"y")
    async with session_factory() as s:
        for rel in ("Models/foo.stl", "back\\slash.stl", "gone.stl"):
            s.add(UploadedFile(original_filename=Path(rel).name, relative_path=rel, folder="/", size_bytes=1,
                               content_hash=f"h-{rel}", mtime=1.0, plates=[], missing=False, uploaded_at="2026-01-01"))
        await s.commit()
        (lib_dir / "gone.stl").unlink()
        summary = await LibraryScanner(s, lib_dir, cache_dir).scan()
        rows = {r.relative_path: r for r in (await s.execute(select(UploadedFile))).scalars()}
    assert set(rows) == {"Models/foo.stl", "back\\slash.stl"}        # only the truly vanished row was removed
    assert not rows["Models/foo.stl"].missing and not rows["back\\slash.stl"].missing
    assert (summary["removed"], summary["missing"], summary["added"]) == (1, 0, 0)


async def test_a_vanished_file_is_removed_from_the_index(tmp_path, session_factory):
    lib, cache = InMemoryArtifactStore(), InMemoryArtifactStore()
    lib.put("a.stl", io.BytesIO(b"x"))
    async with session_factory() as s:
        sc = _scanner(s, lib, cache, tmp_path)
        await sc.scan()
        lib.delete("a.stl")
        assert (await sc.scan())["removed"] == 1


# ---- parse_plates scratch dir -----------------------------------------------------------------------------------

def _record_scratch(seen):
    real = LibraryScanner._parse_local

    def spy(abs_path, thumb_dir):
        seen.append(thumb_dir)
        assert thumb_dir.is_dir()
        return real(abs_path, thumb_dir)
    return spy


def test_parse_plates_removes_its_scratch_dir_on_success(tmp_path):
    lib, cache, seen = InMemoryArtifactStore(), InMemoryArtifactStore(), []
    lib.put("m.3mf", io.BytesIO(_3mf()))
    sc = LibraryScanner(None, tmp_path / "l", tmp_path / "c", library_store=lib, filecache_store=cache)
    with patch.object(LibraryScanner, "_parse_local", staticmethod(_record_scratch(seen))):
        sc.parse_plates("m.3mf", 1)
    assert len(seen) == 1 and not seen[0].exists()


def test_parse_plates_removes_its_scratch_dir_when_parsing_raises(tmp_path):
    lib, cache, seen = InMemoryArtifactStore(), InMemoryArtifactStore(), []
    lib.put("m.3mf", io.BytesIO(_3mf()))
    sc = LibraryScanner(None, tmp_path / "l", tmp_path / "c", library_store=lib, filecache_store=cache)

    def boom(abs_path, thumb_dir):
        seen.append(thumb_dir)
        raise RuntimeError("bad zip")
    with patch.object(LibraryScanner, "_parse_local", staticmethod(boom)), pytest.raises(RuntimeError):
        sc.parse_plates("m.3mf", 1)
    assert len(seen) == 1 and not seen[0].exists()


# ---- local-disk store: only valid keys are listed -------------------------------------------------------------

def test_list_skips_names_that_are_not_valid_keys_and_symlinks_out_of_the_root(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside.stl"
    store = LocalDiskArtifactStore(root)
    store.put("ok.stl", io.BytesIO(b"1"))
    (root / "back\\slash.stl").write_bytes(b"2")
    outside.write_bytes(b"3")
    (root / "link.stl").symlink_to(outside)
    assert [i.key for i in store.list()] == ["ok.stl"]


# ---- path-bound readers via materialize -----------------------------------------------------------------------

def test_with_local_path_reads_a_materialized_copy_and_defaults_when_absent():
    store = InMemoryArtifactStore()
    store.put("a.3mf", io.BytesIO(b"data"))
    seen: list[str] = []
    assert with_local_path(store, "a.3mf", lambda p: (seen.append(p), Path(p).read_bytes())[1], b"") == b"data"
    assert Path(seen[0]).name == "a.3mf" and not Path(seen[0]).exists()           # temp copy removed after the block
    assert with_local_path(store, "missing.3mf", lambda p: 1 / 0, "default") == "default"
    assert with_local_path(store, "../bad", lambda p: 1 / 0, "default") == "default"


async def test_model_filaments_and_embedded_settings_read_through_the_store(client, upload_3mf, tmp_path):
    import json
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps({"filament_colour": ["#FF0000"], "filament_type": ["PLA"]}))
        zf.writestr("Metadata/plate_1.png", PNG)
    file_id = await upload_3mf("proj.3mf", buf.getvalue())
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"):
        fil = await client.get(f"/api/v1/files/{file_id}/model-filaments")
        assert fil.status_code == 200 and fil.json()[0]["type"] == "PLA"
        assert (await client.get(f"/api/v1/files/{file_id}/embedded-settings")).status_code == 200
        (tmp_path / "library" / "Job Uploads" / "proj.3mf").unlink()
        assert (await client.get(f"/api/v1/files/{file_id}/model-filaments")).json() == []
        assert (await client.get(f"/api/v1/files/{file_id}/embedded-settings")).json() == []


async def test_upload_stores_the_thumbnail_as_a_filecache_key_and_serves_it(client, upload_3mf, tmp_path):
    file_id = await upload_3mf("m.3mf", make_3mf_bytes())
    plates = (await client.get(f"/api/v1/files/{file_id}/plates")).json()["plates"]
    assert plates[0]["thumbnail_path"] == f"{file_id}/thumbnails/plate_1.png"
    assert (tmp_path / "filecache" / str(file_id) / "thumbnails" / "plate_1.png").read_bytes() == b"\x89PNG"
    with patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"):
        resp = await client.get(f"/api/v1/files/{file_id}/thumbnails/plate_1.png")
    assert resp.status_code == 200 and resp.content == b"\x89PNG"
    listed = (await client.get("/api/v1/files")).json()[0]["plate_thumbnails"]
    assert listed[0]["thumbnail_url"] == f"/api/v1/files/{file_id}/thumbnails/plate_1.png"


def test_gcode_estimate_of_a_missing_file_is_no_estimate(tmp_path):
    from types import SimpleNamespace
    from app.api.routes.jobs import _apply_gcode_estimate
    job = SimpleNamespace(plate_number=1, estimate_status="stale")
    with patch("app.config.get_library_dir", return_value=tmp_path):
        _apply_gcode_estimate(job, SimpleNamespace(relative_path="gone.gcode"))
    assert job.estimate_status is None
