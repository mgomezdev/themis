"""Library + filecache artifact stores and the collision-naming policy on top of them (BIZ-285).

Both stores are rebuilt per call from `config` (cheap; tests and `THEMIS_*_DIR` overrides take effect immediately).
The library store keeps emptied folders (they are user-visible objects: `POST/DELETE /files/folders`). Blocking API:
call from a worker thread. Design: docs/architecture/artifact-storage.md.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, TypeVar

from .. import config
from .artifact_store import (
    ArtifactExists, ArtifactInfo, ArtifactNotFound, ArtifactStore, InvalidArtifactKey, LocalDiskArtifactStore, PART_SUFFIX,
)
from .library_scanner import name_candidates


def get_library_store(*, prune_empty: bool = False) -> LocalDiskArtifactStore:
    """`prune_empty=True` only for flows that stage under a throwaway folder and move out of it (the pack output): the
    scratch folder then vanishes with its last artifact instead of lingering as a user-visible folder."""
    return LocalDiskArtifactStore(config.get_library_dir(), prune_empty=prune_empty)


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


def put_file_unique(store: ArtifactStore, src: Path | str, folder: str, filename: str) -> ArtifactInfo:
    """Publish the local file `src` as a new artifact in `folder` under the first free name (`name`, `name (2)`, ...). The
    store's create is exclusive, so two concurrent publishers can never pick (and clobber) the same name: the loser sees
    `ArtifactExists` and takes the next candidate. Blocking: call from a worker thread."""
    for name in name_candidates(filename):
        key = join_key(folder, name)
        if store.exists(key):   # skip the copy for a taken name; the exclusive create below still decides races
            continue
        try:
            return store.put_file(key, src)
        except ArtifactExists:
            continue
    raise AssertionError("unreachable")


T = TypeVar("T")


def with_local_path(store: ArtifactStore, key: str, fn: Callable[[str], T], default: T) -> T:
    """Run a path-bound reader (`zipfile`, the format provider) on `key` via `materialize`; `default` when the artifact is
    absent or its key is unusable (what those readers already return for a missing file). Blocking: call from a worker thread."""
    try:
        with store.materialize(key) as path:
            return fn(str(path))
    except (ArtifactNotFound, InvalidArtifactKey):
        return default
