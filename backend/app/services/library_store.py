"""Library + filecache artifact stores and the collision-naming policy on top of them (BIZ-285).

Both stores are rebuilt per call from `config` (cheap; tests and `THEMIS_*_DIR` overrides take effect immediately).
The library store keeps emptied folders (they are user-visible objects: `POST/DELETE /files/folders`). Blocking API:
call from a worker thread. Design: docs/architecture/artifact-storage.md.
"""
from __future__ import annotations

from .. import config
from .artifact_store import ArtifactExists, ArtifactInfo, ArtifactStore, LocalDiskArtifactStore, PART_SUFFIX
from .library_scanner import name_candidates


def get_library_store() -> LocalDiskArtifactStore:
    return LocalDiskArtifactStore(config.get_library_dir(), prune_empty=False)


def get_filecache_store() -> LocalDiskArtifactStore:
    return LocalDiskArtifactStore(config.get_filecache_dir())


def is_reserved_name(name: str) -> bool:
    """Names the store hides from `list` (in-flight temps). A real artifact called `x.part` would be invisible."""
    return name.endswith(PART_SUFFIX)


def join_key(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder else name


def folder_key(key: str) -> str:
    """Parent folder of a key ('' for the root)."""
    return key.rpartition("/")[0]


def key_name(key: str) -> str:
    return key.rpartition("/")[2]


def unique_key(store: ArtifactStore, folder: str, filename: str) -> str:
    """First free `folder/<filename>` / `<stem> (n)<ext>` key (advisory: use `move_unique` to claim one race-free)."""
    for name in name_candidates(filename):
        key = join_key(folder, name)
        if not store.exists(key):
            return key
    raise AssertionError("unreachable")


def move_unique(store: ArtifactStore, src_key: str, folder: str, filename: str) -> ArtifactInfo:
    """Move `src_key` to a free name in `folder`. The store's move is exclusive, so a name taken in the meantime
    (`ArtifactExists`, including by a directory) just advances to the next candidate."""
    for name in name_candidates(filename):
        key = join_key(folder, name)
        if store.exists(key):
            continue
        try:
            return store.move(src_key, key)
        except ArtifactExists:
            continue
    raise AssertionError("unreachable")
