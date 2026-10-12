"""Collision naming on top of the artifact store (BIZ-285)."""
from __future__ import annotations

import io
from unittest.mock import patch

from app.services.artifact_store import ArtifactExists, LocalDiskArtifactStore
from app.services.library_store import folder_key, is_reserved_name, key_name, move_unique, unique_key


def _store(tmp_path):
    return LocalDiskArtifactStore(tmp_path / "lib", prune_empty=False)


def test_unique_key_numbers_collisions_and_keeps_the_sliced_archive_suffix(tmp_path):
    s = _store(tmp_path)
    s.put("F/a.3mf", io.BytesIO(b"1"))
    s.put("F/x.gcode.3mf", io.BytesIO(b"1"))
    assert unique_key(s, "F", "a.3mf") == "F/a (2).3mf"
    assert unique_key(s, "F", "x.gcode.3mf") == "F/x (2).gcode.3mf"   # not "x.gcode (2).3mf"
    assert unique_key(s, "", "fresh.stl") == "fresh.stl"


def test_move_unique_skips_a_directory_squatting_on_the_name(tmp_path):
    s = _store(tmp_path)
    s.put("scratch", io.BytesIO(b"d"))
    s.put("F/deep/inner.stl", io.BytesIO(b"1"))   # "F/deep" is a directory-like prefix
    info = move_unique(s, "scratch", "F", "deep")
    assert info.key == "F/deep (2)" and s.exists("F/deep (2)") and not s.exists("scratch")


def test_move_unique_advances_when_the_name_is_taken_during_the_move(tmp_path):
    s = _store(tmp_path)
    s.put("scratch", io.BytesIO(b"d"))
    real = LocalDiskArtifactStore.move
    seen = []

    def racing(self, src, dst, **kw):
        seen.append(dst)
        if len(seen) == 1:
            raise ArtifactExists(dst)   # someone claimed it between exists() and move()
        return real(self, src, dst, **kw)

    with patch.object(LocalDiskArtifactStore, "move", racing):
        assert move_unique(s, "scratch", "F", "a.stl").key == "F/a (2).stl"
    assert seen == ["F/a.stl", "F/a (2).stl"]


def test_key_helpers():
    assert (folder_key("a/b/c.stl"), key_name("a/b/c.stl"), folder_key("c.stl")) == ("a/b", "c.stl", "")
    assert is_reserved_name("x.stl.part") and not is_reserved_name("x.stl")
