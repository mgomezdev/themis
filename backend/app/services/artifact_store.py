"""Artifact storage boundary (BIZ-256). Design + consumer inventory: docs/architecture/artifact-storage.md.

The library CRUD routes (`api/routes/files.py`, via `services/library_store.py`) use it (BIZ-285). Every other live caller
still resolves real filesystem paths (`config.get_library_dir()` + `library_abs_path`, `get_filecache_dir()`,
`get_data_dir()`); they are classified in the design note. `LocalDiskArtifactStore` is the reference backend and is deliberately byte- and name-compatible with that
layout: key == the `relative_path` already stored in `uploaded_files`, under the same root. No remote backend exists or is
decided here (CLAUDE.md / BIZ-265 non-goal).

Blocking API: call from a worker thread (`asyncio.to_thread`), never the event loop (same rule as the slicer).
"""
from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

CHUNK_SIZE = 1024 * 1024
PART_SUFFIX = ".part"   # in-flight writes; never listed, never visible under a final key
TEMP_NAME_BYTES = 100   # at most this much of the final name goes into the temp name (BIZ-285): keeps `.<name>.<rand>.part` < 255


class ArtifactError(Exception):
    """Base for storage failures. Backends map their native errors onto the subclasses below."""


class InvalidArtifactKey(ArtifactError):
    """Key is empty, absolute, contains `..`/`.`/empty segments, backslashes or NULs, or resolves outside the root."""


class ArtifactNotFound(ArtifactError):
    """No artifact under that key (also: a directory-like prefix where an object was required)."""


class ArtifactExists(ArtifactError):
    """Exclusive create (`overwrite=False`) or `move` onto an existing key."""


class ArtifactIntegrityError(ArtifactError):
    """Bytes written do not match the caller's `expected_sha256`; nothing was committed."""


class ArtifactStoreError(ArtifactError):
    """Backend I/O failure (disk full, permissions, network). The target key is unchanged unless stated otherwise."""


@dataclass(frozen=True)
class ArtifactInfo:
    key: str
    size: int
    mtime: float
    sha256: str | None = None   # set by writes (computed while streaming); `stat`/`list` leave it None (hashing = a full read)


def validate_key(key: str) -> str:
    """A key is a '/'-separated relative path of non-empty segments, none `.` or `..`. Returned unchanged."""
    if not isinstance(key, str) or not key or key.startswith("/") or "\\" in key or "\0" in key:
        raise InvalidArtifactKey(f"invalid artifact key: {key!r}")
    for seg in key.split("/"):
        if seg in ("", ".", ".."):
            raise InvalidArtifactKey(f"invalid artifact key: {key!r}")
    return key


class ArtifactWriter:
    """Handle yielded by `ArtifactStore.writer`. `write()` streams; the artifact appears atomically at context exit
    (and `info` is then set), or not at all if the block raised."""

    def __init__(self, sink: BinaryIO, expected_sha256: str | None):
        self._sink = sink
        self._hash = hashlib.sha256()
        self._size = 0
        self._expected = expected_sha256
        self.info: ArtifactInfo | None = None

    def write(self, data: bytes) -> None:
        self._sink.write(data)
        self._hash.update(data)
        self._size += len(data)

    @property
    def size(self) -> int:
        return self._size

    def _digest(self) -> str:
        digest = self._hash.hexdigest()
        if self._expected is not None and digest != self._expected.lower():
            raise ArtifactIntegrityError(f"sha256 mismatch: expected {self._expected}, got {digest}")
        return digest


class ArtifactStore(ABC):
    """Keyed blob store. Semantics every backend must honour (pinned by tests/services/test_artifact_store_contract.py):

    - **Atomic publish.** A key is either absent, or holds a complete artifact. `writer`/`put`/`put_file` never expose
      partial bytes; a failure or exception leaves the previous content (or absence) and no stray temp data.
    - **Exclusive by default.** `overwrite=False` raises `ArtifactExists` instead of replacing, race-free.
    - **Streaming.** Nothing requires a whole artifact in memory; reads/writes are chunked.
    - **Flat namespace with '/' keys.** Directories are not objects: `list(prefix)` is by key prefix, `delete_prefix`
      removes a subtree, and an empty "folder" has no existence of its own.
    """

    # --- write -----------------------------------------------------------------------------------------------
    @abstractmethod
    def writer(self, key: str, *, overwrite: bool = False, expected_sha256: str | None = None
               ) -> contextlib.AbstractContextManager[ArtifactWriter]:
        """Context manager for a streamed, atomic write. Raises `ArtifactExists` (at exit) when `overwrite=False` and the
        key exists, `ArtifactIntegrityError` on a hash mismatch; in every error case nothing is committed."""

    def put(self, key: str, data: BinaryIO, *, overwrite: bool = False, expected_sha256: str | None = None) -> ArtifactInfo:
        with self.writer(key, overwrite=overwrite, expected_sha256=expected_sha256) as w:
            while chunk := data.read(CHUNK_SIZE):
                w.write(chunk)
        assert w.info is not None
        return w.info

    def put_file(self, key: str, path: Path | str, *, overwrite: bool = False, expected_sha256: str | None = None) -> ArtifactInfo:
        """Publish a local file (a slicer/packer output) as an artifact; the source is left in place."""
        with open(path, "rb") as fh:
            return self.put(key, fh, overwrite=overwrite, expected_sha256=expected_sha256)

    # --- read ------------------------------------------------------------------------------------------------
    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        """Binary read stream (caller closes). `ArtifactNotFound` if absent. Later overwrites do not corrupt an open reader."""

    @abstractmethod
    def stat(self, key: str) -> ArtifactInfo:
        """Size + mtime (no hash). `ArtifactNotFound` if absent."""

    def exists(self, key: str) -> bool:
        try:
            self.stat(key)
        except ArtifactNotFound:
            return False
        return True

    @abstractmethod
    def list(self, prefix: str = "") -> Iterator[ArtifactInfo]:
        """Every artifact whose key starts with `prefix` (a whole-segment prefix: "a/b" matches "a/b/c", not "a/bc"),
        sorted by key, excluding in-flight writes. Empty `prefix` = everything."""

    def sha256(self, key: str) -> str:
        h = hashlib.sha256()
        with self.open(key) as fh:
            while chunk := fh.read(CHUNK_SIZE):
                h.update(chunk)
        return h.hexdigest()

    @abstractmethod
    def materialize(self, key: str) -> contextlib.AbstractContextManager[Path]:
        """Context manager yielding a real, **read-only** local path for path-only consumers (slicer, `zipfile`, external binaries).
        The path's final segment equals the key's (file kind is detected by suffix, e.g. `.gcode.3mf`). Local backends
        yield the stored file itself (no copy, no cleanup); remote backends download to a private temp dir that is removed
        on exit, success or error. The path is invalid after the block; callers must not write to it. On local disk it is the LIVE
        stored file: a write to it, or an overwrite/delete by another caller meanwhile, affects the artifact itself."""

    # --- mutate ----------------------------------------------------------------------------------------------
    @abstractmethod
    def delete(self, key: str, *, missing_ok: bool = False) -> None:
        """Remove one artifact. `ArtifactNotFound` unless `missing_ok`."""

    @abstractmethod
    def delete_prefix(self, prefix: str) -> int:
        """Remove every artifact under the whole-segment `prefix` (and any now-empty containers). Returns the count.
        Idempotent: nothing there -> 0."""

    @abstractmethod
    def move(self, src: str, dst: str, *, overwrite: bool = False) -> ArtifactInfo:
        """Rename. Atomic on local disk; backends without rename do copy-then-delete and document the non-atomic window
        (`dst` complete before `src` goes). `ArtifactNotFound` for `src`, `ArtifactExists` for `dst` unless `overwrite`."""


# ── local-disk reference backend ──────────────────────────────────────────────────────────────────────────────────

class LocalDiskArtifactStore(ArtifactStore):
    """Artifacts as plain files under `root`; key `a/b/c.3mf` is `root/a/b/c.3mf`. Same names, same layout, same
    umask-governed permissions as the code it will eventually replace, so an existing library is a valid store as-is."""

    def __init__(self, root: Path | str, *, prune_empty: bool = True):
        """`prune_empty=False` keeps a folder when its last artifact leaves (the library: folders are user-visible
        objects created by `POST /files/folders`). Default True matches the flat-namespace contract (filecache)."""
        self.root = Path(root)
        self._prune = prune_empty
        self.root.mkdir(parents=True, exist_ok=True)
        self._root_resolved = self.root.resolve()

    # -- paths
    def _path(self, key: str) -> Path:
        validate_key(key)
        path = self.root / key
        resolved = path.resolve()   # follows symlinks: one that leaves the root is refused, same as files._safe_subpath
        if self._root_resolved not in resolved.parents:
            raise InvalidArtifactKey(f"artifact key escapes the store root: {key!r}")
        return path

    def local_path(self, key: str) -> Path:
        """The real filesystem path for `key` (existing or not). **Local-only escape hatch, intentionally not on the ABC**:
        it exists so today's path-bound callers can be migrated one at a time; a remote backend cannot offer it."""
        return self._path(key)

    def _info(self, key: str, path: Path, sha256: str | None = None) -> ArtifactInfo:
        st = path.stat()
        return ArtifactInfo(key=key, size=st.st_size, mtime=st.st_mtime, sha256=sha256)

    # -- write
    @contextlib.contextmanager
    def writer(self, key: str, *, overwrite: bool = False, expected_sha256: str | None = None) -> Iterator[ArtifactWriter]:
        dest = self._path(key)
        if dest.is_dir():
            raise ArtifactExists(f"{key!r} is a directory-like prefix")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{self._temp_stem(dest.name)}.", suffix=PART_SUFFIX)
        except OSError as exc:
            raise ArtifactStoreError(f"cannot write {key!r}: {exc}") from exc
        tmp = Path(tmp_name)
        # mkstemp is 0600; restore the umask-governed mode a plain open() would give (matches slice_saver's copies).
        umask = os.umask(0)
        os.umask(umask)
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o666 & ~umask)
        in_body = False
        try:
            with os.fdopen(fd, "wb") as sink:
                w = ArtifactWriter(sink, expected_sha256)
                in_body = True
                yield w
                in_body = False
                sink.flush()
                os.fsync(sink.fileno())   # durable before it becomes visible
            digest = w._digest()
            self._publish(tmp, dest, overwrite, key)
            self._fsync_dir(dest.parent)
            w.info = self._info(key, dest, digest)
        except ArtifactError:
            raise
        except OSError as exc:
            if in_body:
                raise   # the caller's own error (e.g. a dropped upload stream) is theirs, not a storage failure
            raise ArtifactStoreError(f"cannot write {key!r}: {exc}") from exc
        finally:
            tmp.unlink(missing_ok=True)   # no-op once published

    @staticmethod
    def _temp_stem(name: str) -> str:
        raw = name.encode("utf-8")
        if len(raw) <= TEMP_NAME_BYTES:
            return name
        return raw[:TEMP_NAME_BYTES].decode("utf-8", errors="ignore")   # never split a multibyte character

    @staticmethod
    def _publish(tmp: Path, dest: Path, overwrite: bool, key: str) -> None:
        if overwrite:
            os.replace(tmp, dest)   # atomic; open readers keep the old inode
            return
        try:
            os.link(tmp, dest)      # atomic exclusive create: fails if dest exists, never exposes partial bytes
        except FileExistsError:
            raise ArtifactExists(f"artifact already exists: {key!r}") from None
        except OSError as exc:
            if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.EMLINK):
                raise
            # Filesystem without hard links (some SMB/FAT mounts): reserve the name exclusively, then rename over it.
            try:
                os.close(os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666))
            except FileExistsError:
                raise ArtifactExists(f"artifact already exists: {key!r}") from None
            os.replace(tmp, dest)

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        with contextlib.suppress(OSError):   # best effort: not supported on every platform/filesystem
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    # -- read
    def open(self, key: str) -> BinaryIO:
        path = self._path(key)
        try:
            return open(path, "rb")
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            raise ArtifactNotFound(key) from None
        except OSError as exc:
            raise ArtifactStoreError(f"cannot read {key!r}: {exc}") from exc

    def stat(self, key: str) -> ArtifactInfo:
        path = self._path(key)
        if not path.is_file():
            raise ArtifactNotFound(key)
        try:
            return self._info(key, path)
        except FileNotFoundError:
            raise ArtifactNotFound(key) from None

    def list(self, prefix: str = "") -> Iterator[ArtifactInfo]:
        base = self.root
        if prefix:
            validate_key(prefix)
            base = self._path(prefix)
        found: list[ArtifactInfo] = []
        if base.is_file():
            return iter([self._info(prefix, base)])
        if base.is_dir():
            for p in base.rglob("*"):
                if p.is_file() and not p.name.endswith(PART_SUFFIX):
                    key = p.relative_to(self.root).as_posix()
                    try:
                        found.append(self._info(key, p))
                    except FileNotFoundError:
                        continue   # deleted between walk and stat
        return iter(sorted(found, key=lambda i: i.key))

    @contextlib.contextmanager
    def materialize(self, key: str) -> Iterator[Path]:
        path = self._path(key)
        if not path.is_file():
            raise ArtifactNotFound(key)
        yield path   # the stored file itself: zero copy, nothing to clean up

    # -- mutate
    def delete(self, key: str, *, missing_ok: bool = False) -> None:
        path = self._path(key)
        if not path.is_file():
            if missing_ok:
                return
            raise ArtifactNotFound(key)
        try:
            path.unlink()
        except FileNotFoundError:
            if not missing_ok:
                raise ArtifactNotFound(key) from None
        except OSError as exc:
            raise ArtifactStoreError(f"cannot delete {key!r}: {exc}") from exc
        self._prune_empty(path.parent)

    def delete_prefix(self, prefix: str) -> int:
        validate_key(prefix)
        target = self._path(prefix)
        if target.is_file():
            self.delete(prefix)
            return 1
        if not target.is_dir():
            return 0
        count = sum(1 for p in target.rglob("*") if p.is_file())
        try:
            shutil.rmtree(target)
        except OSError as exc:
            raise ArtifactStoreError(f"cannot delete prefix {prefix!r}: {exc}") from exc
        self._prune_empty(target.parent)
        return count

    def _prune_empty(self, directory: Path) -> None:
        # Directories are not objects, so an emptied folder disappears with its last artifact (never the root itself).
        if not self._prune:
            return
        while directory != self.root and self._root_resolved in directory.resolve().parents:
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent

    def move(self, src: str, dst: str, *, overwrite: bool = False) -> ArtifactInfo:
        s, d = self._path(src), self._path(dst)
        if not s.is_file():
            raise ArtifactNotFound(src)
        if d.is_dir():
            raise ArtifactExists(f"{dst!r} is a directory-like prefix")
        if not overwrite and d.exists():
            raise ArtifactExists(f"artifact already exists: {dst!r}")
        try:
            d.parent.mkdir(parents=True, exist_ok=True)
            if overwrite:
                os.replace(s, d)
            else:
                self._publish_link_move(s, d, dst)
        except ArtifactError:
            raise
        except OSError as exc:
            raise ArtifactStoreError(f"cannot move {src!r} -> {dst!r}: {exc}") from exc
        self._prune_empty(s.parent)
        return self._info(dst, d)

    def _publish_link_move(self, s: Path, d: Path, dst: str) -> None:
        # Exclusive rename: link + unlink so a racing writer to `dst` is never clobbered.
        self._publish(s, d, False, dst)
        s.unlink(missing_ok=True)   # the no-hardlink fallback has already renamed s away

