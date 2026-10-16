"""LocalDiskArtifactStore specifics (BIZ-256): it must stay layout-compatible with today's library/filecache so existing
data is a valid store without migration, and must keep the safety properties the path-based code relies on."""
from __future__ import annotations

import errno
import io
import os
import stat
import sys

import pytest
from sqlalchemy import select

from app.models import UploadedFile
from app.services import artifact_store as mod
from app.services.artifact_store import (
    ArtifactExists, ArtifactNotFound, ArtifactStoreError, InvalidArtifactKey, LocalDiskArtifactStore,
)
from app.services.library_scanner import LibraryScanner, library_abs_path


@pytest.fixture
def root(tmp_path):
    return tmp_path / "library"


@pytest.fixture
def store(root):
    return LocalDiskArtifactStore(root)


def test_key_is_exactly_the_relative_path_under_root(store, root):
    store.put("Job Uploads/benchy (2).3mf", io.BytesIO(b"abc"))
    assert (root / "Job Uploads" / "benchy (2).3mf").read_bytes() == b"abc"
    assert store.local_path("Job Uploads/benchy (2).3mf") == library_abs_path(root, "Job Uploads/benchy (2).3mf")


def test_existing_library_files_are_readable_without_migration(store, root):
    (root / "From Printers").mkdir(parents=True)
    (root / "From Printers" / "old.stl").write_bytes(b"solid")
    assert store.stat("From Printers/old.stl").size == 5
    assert [i.key for i in store.list("From Printers")] == ["From Printers/old.stl"]
    with store.open("From Printers/old.stl") as fh:
        assert fh.read() == b"solid"


@pytest.mark.asyncio
async def test_library_scanner_indexes_store_written_files(store, root, tmp_path, session_factory):
    store.put("Parts/cube.stl", io.BytesIO(b"solid cube"))
    store.put("Parts/ignored.txt", io.BytesIO(b"x"))
    async with session_factory() as s:
        summary = await LibraryScanner(s, root, tmp_path / "filecache").scan()
        rows = (await s.execute(select(UploadedFile))).scalars().all()
    assert summary["added"] == 1
    assert [(r.relative_path, r.size_bytes) for r in rows] == [("Parts/cube.stl", 10)]


def test_no_part_files_survive_success_or_failure_and_are_never_listed(store, root):
    store.put("a/ok.3mf", io.BytesIO(b"1"))
    with pytest.raises(RuntimeError):
        with store.writer("a/bad.3mf") as w:
            w.write(b"x")
            (root / "a" / ".stray.part").write_bytes(b"in flight elsewhere")   # a concurrent writer's temp
            raise RuntimeError
    with pytest.raises(ArtifactExists):
        store.put("a/ok.3mf", io.BytesIO(b"2"))
    assert [p.name for p in (root / "a").iterdir() if p.name != ".stray.part"] == ["ok.3mf"]
    assert [i.key for i in store.list()] == ["a/ok.3mf"]       # in-flight temp never listed


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits; Windows has no umask")
def test_file_mode_follows_umask_like_a_plain_open(store, root):
    old = os.umask(0o027)
    try:
        store.put("m/x.gcode", io.BytesIO(b"1"))
        store.put("m/y.gcode", io.BytesIO(b"1"), overwrite=True)
    finally:
        os.umask(old)
    for name in ("x.gcode", "y.gcode"):
        assert stat.S_IMODE((root / "m" / name).stat().st_mode) == 0o640


def test_durability_fsyncs_file_before_it_becomes_visible(store, root, monkeypatch):
    seen: list[bool] = []
    real_fsync = os.fsync
    def spy(fd):
        seen.append(not (root / "d" / "x.3mf").exists())      # still invisible when the data is flushed
        return real_fsync(fd)
    monkeypatch.setattr(mod.os, "fsync", spy)
    store.put("d/x.3mf", io.BytesIO(b"data"))
    assert seen and seen[0] is True


def test_exclusive_create_falls_back_when_filesystem_has_no_hard_links(store, root, monkeypatch):
    def no_link(*a, **k):
        raise OSError(errno.EPERM, "links unsupported")
    monkeypatch.setattr(mod.os, "link", no_link)
    store.put("h/a.3mf", io.BytesIO(b"1"))
    with pytest.raises(ArtifactExists):
        store.put("h/a.3mf", io.BytesIO(b"2"))
    assert (root / "h" / "a.3mf").read_bytes() == b"1"
    assert [p.name for p in (root / "h").iterdir()] == ["a.3mf"]


def test_symlink_escape_and_dir_keys_are_refused(store, root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.3mf").write_bytes(b"s")
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InvalidArtifactKey):
        store.open("link/secret.3mf")
    with pytest.raises(InvalidArtifactKey):
        store.put("link/new.3mf", io.BytesIO(b"x"))
    assert not (outside / "new.3mf").exists()
    store.put("dir/f.3mf", io.BytesIO(b"1"))
    with pytest.raises(ArtifactNotFound):
        store.open("dir")
    with pytest.raises(ArtifactExists):
        store.put("dir", io.BytesIO(b"x"))


def test_materialize_is_zero_copy_the_stored_file_itself(store):
    store.put("z/p.gcode.3mf", io.BytesIO(b"zip"))
    with store.materialize("z/p.gcode.3mf") as p:
        assert p == store.local_path("z/p.gcode.3mf") and p.read_bytes() == b"zip"
    assert p.exists()                                           # nothing to clean up: the artifact stays


@pytest.mark.skipif(sys.platform == "win32", reason="Windows refuses to replace a file that is open; see the next test")
def test_overwrite_does_not_disturb_an_open_reader(store):
    store.put("r/f.3mf", io.BytesIO(b"old-content"))
    with store.open("r/f.3mf") as fh:
        store.put("r/f.3mf", io.BytesIO(b"new"), overwrite=True)
        assert fh.read() == b"old-content"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows refuses to replace a file that is open")
def test_windows_refuses_overwrite_of_an_open_reader_and_keeps_old_bytes(store, root):
    store.put("r/f.3mf", io.BytesIO(b"old-content"))
    with store.open("r/f.3mf") as fh:
        with pytest.raises(ArtifactStoreError):
            store.put("r/f.3mf", io.BytesIO(b"new"), overwrite=True)
        assert fh.read() == b"old-content"
    assert (root / "r" / "f.3mf").read_bytes() == b"old-content"
    assert [p.name for p in (root / "r").iterdir()] == ["f.3mf"]     # the failed write's temp was cleaned up


def test_emptied_folders_disappear_but_root_stays(store, root):
    store.put("a/b/c.png", io.BytesIO(b"1"))
    store.delete("a/b/c.png")
    assert not (root / "a").exists() and root.is_dir()
    store.put("t/1/x.png", io.BytesIO(b"1"))
    store.put("t/2/y.png", io.BytesIO(b"1"))
    assert store.delete_prefix("t/1") == 1
    assert (root / "t" / "2" / "y.png").exists() and not (root / "t" / "1").exists()
    store.move("t/2/y.png", "u/y.png")
    assert not (root / "t").exists()


def test_io_failure_is_mapped_to_store_error(store, root, monkeypatch):
    def deny(*a, **k):
        raise PermissionError(errno.EACCES, "denied")
    monkeypatch.setattr(mod.tempfile, "mkstemp", deny)
    with pytest.raises(ArtifactStoreError):
        store.put("p/x.3mf", io.BytesIO(b"1"))


def test_move_succeeds_on_a_filesystem_without_hard_links(store, root, monkeypatch):
    def no_link(*a, **k):
        raise OSError(errno.EPERM, "links unsupported")
    monkeypatch.setattr(mod.os, "link", no_link)
    store.put("a/src.3mf", io.BytesIO(b"payload"))
    store.put("b/taken.3mf", io.BytesIO(b"other"))
    info = store.move("a/src.3mf", "c/dst.3mf")
    assert info.key == "c/dst.3mf" and (root / "c" / "dst.3mf").read_bytes() == b"payload"
    assert not store.exists("a/src.3mf") and not (root / "a").exists()
    with pytest.raises(ArtifactExists):
        store.move("c/dst.3mf", "b/taken.3mf")
    assert (root / "c" / "dst.3mf").read_bytes() == b"payload" and (root / "b" / "taken.3mf").read_bytes() == b"other"


# ---- BIZ-285 decisions ---------------------------------------------------------------------------------------------

def test_a_key_near_the_255_byte_limit_can_be_written(store, root):
    """The in-flight temp name is derived from the key; it must be capped so the final name is the only limit."""
    name = "n" * 250 + ".stl"   # 254 bytes
    store.put(f"Folder/{name}", io.BytesIO(b"solid"))
    assert (root / "Folder" / name).read_bytes() == b"solid"
    assert [p.name for p in (root / "Folder").iterdir()] == [name]


def test_a_multibyte_name_is_truncated_on_a_character_boundary(store, root):
    name = "\u00e9" * 120 + ".stl"   # 244 bytes
    store.put(name, io.BytesIO(b"x"))
    assert (root / name).exists()


def test_folders_can_be_kept_when_their_last_artifact_goes(root):
    keep = LocalDiskArtifactStore(root, prune_empty=False)
    keep.put("A/B/x.stl", io.BytesIO(b"1"))
    keep.put("A/B/y.stl", io.BytesIO(b"1"))
    keep.put("D/z.stl", io.BytesIO(b"1"))
    keep.move("A/B/x.stl", "C/x.stl")
    keep.delete("A/B/y.stl")
    assert (root / "A" / "B").is_dir()
    assert keep.delete_prefix("D/z.stl") == 1 and (root / "D").is_dir()


def test_default_still_prunes_emptied_folders(store, root):
    store.put("A/B/x.stl", io.BytesIO(b"1"))
    store.delete("A/B/x.stl")
    assert not (root / "A").exists()
