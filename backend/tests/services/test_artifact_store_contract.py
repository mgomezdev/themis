"""ArtifactStore contract (BIZ-256): one suite, run against the local-disk reference backend AND an in-memory fake, so the
interface can't quietly encode local-disk accidents. Design: docs/architecture/artifact-storage.md."""
from __future__ import annotations

import hashlib
import io
import os
import threading
from pathlib import Path

import pytest

from app.services.artifact_store import (
    ArtifactExists, ArtifactIntegrityError, ArtifactNotFound, ArtifactStoreError, InvalidArtifactKey,
    LocalDiskArtifactStore, validate_key,
)
from tests.fake_artifact_store import InMemoryArtifactStore


@pytest.fixture(params=["local", "memory"])
def store(request, tmp_path):
    return LocalDiskArtifactStore(tmp_path / "root") if request.param == "local" else InMemoryArtifactStore()


def _read(store, key: str) -> bytes:
    with store.open(key) as fh:
        return fh.read()


class _Boom(io.RawIOBase):
    """A stream that yields `good` bytes then fails, like a dropped upload."""
    def __init__(self, good: bytes):
        self._buf = io.BytesIO(good)

    def readable(self):
        return True

    def read(self, n=-1):
        chunk = self._buf.read(n)
        if not chunk:
            raise OSError("connection lost")
        return chunk


# ── normal operations ────────────────────────────────────────────────────────────────────────────────────────────

def test_put_open_stat_roundtrip_reports_hash_and_size(store):
    info = store.put("Job Uploads/a b.3mf", io.BytesIO(b"hello"))
    assert info.key == "Job Uploads/a b.3mf" and info.size == 5
    assert info.sha256 == hashlib.sha256(b"hello").hexdigest()
    assert _read(store, "Job Uploads/a b.3mf") == b"hello"
    st = store.stat("Job Uploads/a b.3mf")
    assert (st.size, st.sha256) == (5, None)       # stat never hashes
    assert store.sha256("Job Uploads/a b.3mf") == info.sha256
    assert store.exists("Job Uploads/a b.3mf") and not store.exists("nope.3mf")


def test_list_is_sorted_whole_segment_prefix_filtered(store):
    for k in ["b/2.stl", "a/1.stl", "a/x/3.stl", "ab/4.stl", "root.stl"]:
        store.put(k, io.BytesIO(k.encode()))
    assert [i.key for i in store.list()] == ["a/1.stl", "a/x/3.stl", "ab/4.stl", "b/2.stl", "root.stl"]
    assert [i.key for i in store.list("a")] == ["a/1.stl", "a/x/3.stl"]        # not "ab/..."
    assert list(store.list("zzz")) == []


def test_exclusive_create_by_default_and_overwrite_replaces(store):
    store.put("k.stl", io.BytesIO(b"one"))
    with pytest.raises(ArtifactExists):
        store.put("k.stl", io.BytesIO(b"two"))
    assert _read(store, "k.stl") == b"one"
    store.put("k.stl", io.BytesIO(b"three!"), overwrite=True)
    assert _read(store, "k.stl") == b"three!" and store.stat("k.stl").size == 6


def test_expected_hash_accepts_match_and_rejects_mismatch_without_commit(store):
    good = hashlib.sha256(b"data").hexdigest()
    store.put("ok.gcode", io.BytesIO(b"data"), expected_sha256=good)
    with pytest.raises(ArtifactIntegrityError):
        store.put("bad.gcode", io.BytesIO(b"data"), expected_sha256="0" * 64)
    assert not store.exists("bad.gcode")


def test_put_file_publishes_a_local_file_and_leaves_the_source(store, tmp_path):
    src = tmp_path / "sliced.gcode"
    src.write_bytes(b"G1 X1\n")
    info = store.put_file("gcode/1/sliced.gcode", src)
    assert _read(store, "gcode/1/sliced.gcode") == b"G1 X1\n" and info.size == 6
    assert src.read_bytes() == b"G1 X1\n"


def test_move_renames_and_refuses_clobber(store):
    store.put("a/old.3mf", io.BytesIO(b"X"))
    store.put("b/taken.3mf", io.BytesIO(b"Y"))
    with pytest.raises(ArtifactExists):
        store.move("a/old.3mf", "b/taken.3mf")
    assert _read(store, "a/old.3mf") == b"X" and _read(store, "b/taken.3mf") == b"Y"
    info = store.move("a/old.3mf", "c/new.3mf")
    assert info.key == "c/new.3mf" and _read(store, "c/new.3mf") == b"X"
    assert not store.exists("a/old.3mf")
    store.move("c/new.3mf", "b/taken.3mf", overwrite=True)
    assert _read(store, "b/taken.3mf") == b"X"


def test_delete_and_delete_prefix(store):
    for k in ["t/1/a.png", "t/1/b.png", "t/12/c.png", "keep.stl"]:
        store.put(k, io.BytesIO(b"."))
    store.delete("keep.stl")
    with pytest.raises(ArtifactNotFound):
        store.delete("keep.stl")
    store.delete("keep.stl", missing_ok=True)
    assert store.delete_prefix("t/1") == 2             # whole-segment: t/12 survives
    assert [i.key for i in store.list()] == ["t/12/c.png"]
    assert store.delete_prefix("t/1") == 0             # idempotent
    assert store.delete_prefix("t/12/c.png") == 1      # a prefix may name one object


# ── large / streamed ─────────────────────────────────────────────────────────────────────────────────────────────

def test_large_artifact_streams_in_bounded_chunks(store):
    size = 12 * 1024 * 1024 + 7
    piece = os.urandom(1024 * 1024)

    class Source(io.RawIOBase):
        def __init__(self):
            self.left, self.max_request = size, 0
        def readable(self):
            return True
        def read(self, n=-1):
            self.max_request = max(self.max_request, n)
            n = min(n, self.left, len(piece))
            self.left -= n
            return piece[:n]

    src, h = Source(), hashlib.sha256()
    expected_left = size
    while expected_left:
        n = min(len(piece), expected_left)
        h.update(piece[:n])
        expected_left -= n
    info = store.put("big/pack.3mf", src)
    assert info.size == size and info.sha256 == h.hexdigest()
    assert 0 < src.max_request <= 1024 * 1024           # never asked for the whole body
    assert store.stat("big/pack.3mf").size == size
    assert store.sha256("big/pack.3mf") == h.hexdigest()


def test_writer_streams_and_publishes_only_at_exit(store):
    with store.writer("w.gcode") as w:
        w.write(b"abc")
        w.write(b"def")
        assert not store.exists("w.gcode")               # invisible until the block ends
    assert w.size == 6 and w.info.size == 6 and _read(store, "w.gcode") == b"abcdef"


# ── missing objects & bad keys ───────────────────────────────────────────────────────────────────────────────────

def test_missing_object_errors(store):
    for op in (lambda: store.open("no.3mf"), lambda: store.stat("no.3mf"), lambda: store.sha256("no.3mf"),
               lambda: store.move("no.3mf", "x.3mf")):
        with pytest.raises(ArtifactNotFound):
            op()
    with pytest.raises(ArtifactNotFound):
        with store.materialize("no.3mf"):
            pass


@pytest.mark.parametrize("key", ["", "/abs.3mf", "a/../b.3mf", "../x", "a//b", "a/./b", "a\\b", "a\0b", "a/", "."])
def test_invalid_keys_rejected_everywhere(store, key):
    with pytest.raises(InvalidArtifactKey):
        validate_key(key)
    with pytest.raises(InvalidArtifactKey):
        store.put(key, io.BytesIO(b"x"))
    with pytest.raises(InvalidArtifactKey):
        store.open(key)
    with pytest.raises(InvalidArtifactKey):
        store.delete(key)


# ── partial failure ──────────────────────────────────────────────────────────────────────────────────────────────

def test_failed_stream_commits_nothing_new(store):
    with pytest.raises(OSError):
        store.put("fail.3mf", _Boom(b"x" * 100))
    assert not store.exists("fail.3mf") and list(store.list()) == []


def test_failed_overwrite_leaves_previous_content_intact(store):
    store.put("keep.3mf", io.BytesIO(b"original"))
    with pytest.raises(OSError):
        store.put("keep.3mf", _Boom(b"partial"), overwrite=True)
    assert _read(store, "keep.3mf") == b"original"


def test_exception_inside_writer_block_aborts_the_write(store):
    with pytest.raises(RuntimeError):
        with store.writer("abort.gcode") as w:
            w.write(b"half")
            raise RuntimeError("slicer died")
    assert not store.exists("abort.gcode") and list(store.list()) == []


def test_exclusive_create_race_has_exactly_one_winner(store):
    results: list[str] = []
    barrier = threading.Barrier(8)

    def go(i: int):
        barrier.wait()
        try:
            store.put("race.3mf", io.BytesIO(f"w{i}".encode()))
            results.append("won")
        except ArtifactExists:
            results.append("lost")

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("won") == 1 and results.count("lost") == 7
    assert len(_read(store, "race.3mf")) == 2          # one writer's complete bytes, never a mix


# ── materialization & cleanup ────────────────────────────────────────────────────────────────────────────────────

def test_materialize_gives_readable_path_keeping_the_name_suffix(store):
    store.put("lib/Plate 1.gcode.3mf", io.BytesIO(b"zipbytes"))
    with store.materialize("lib/Plate 1.gcode.3mf") as p:
        assert isinstance(p, Path) and p.name == "Plate 1.gcode.3mf"   # kind detection is by suffix
        assert p.read_bytes() == b"zipbytes"


def test_in_memory_materialize_cleans_up_on_success_and_error():
    fake = InMemoryArtifactStore()
    fake.put("a/m.3mf", io.BytesIO(b"1"))
    with fake.materialize("a/m.3mf") as p:
        assert p.exists()
    assert not p.exists() and not p.parent.exists()
    with pytest.raises(RuntimeError):
        with fake.materialize("a/m.3mf") as p2:
            raise RuntimeError("slicer crashed")
    assert not p2.parent.exists()
    assert len(fake.materialized_dirs) == 2


def test_in_memory_injected_write_failure_and_delete_failure():
    fake = InMemoryArtifactStore()
    fake.fail_after_bytes = 10
    with pytest.raises(ArtifactStoreError):
        fake.put("big.3mf", io.BytesIO(b"x" * 100))
    assert not fake.exists("big.3mf")
    fake.fail_after_bytes = None
    fake.put("k", io.BytesIO(b"1"))
    fake.fail_delete = True
    with pytest.raises(ArtifactStoreError):
        fake.delete("k")
    assert fake.exists("k")                             # a failed delete leaves the object
