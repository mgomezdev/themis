"""In-memory ArtifactStore (BIZ-256): the second implementation that keeps the contract honest.

It models what a remote backend does and local disk hides: no real paths (`materialize` must copy to a temp dir and
clean it up), copy-then-delete moves, and injectable faults (`fail_after_bytes`, `fail_delete`) for partial-failure tests.
"""
from __future__ import annotations

import contextlib
import io
import threading
import shutil
import tempfile
import time
from pathlib import Path
from typing import BinaryIO, Iterator

from app.services.artifact_store import (
    ArtifactExists, ArtifactInfo, ArtifactNotFound, ArtifactStore, ArtifactStoreError, ArtifactWriter, validate_key,
)


class InMemoryArtifactStore(ArtifactStore):
    def __init__(self) -> None:
        self.blobs: dict[str, tuple[bytes, float]] = {}
        self.fail_after_bytes: int | None = None   # a write raises ArtifactStoreError once this many bytes were written
        self.fail_delete = False
        self._lock = threading.Lock()
        self.materialized_dirs: list[Path] = []     # every temp dir handed out, so tests can assert cleanup

    @contextlib.contextmanager
    def writer(self, key: str, *, overwrite: bool = False, expected_sha256: str | None = None) -> Iterator[ArtifactWriter]:
        validate_key(key)
        buf = _FaultyBuffer(self.fail_after_bytes)
        w = ArtifactWriter(buf, expected_sha256)
        yield w                                   # staged in `buf`; nothing is visible under `key` until commit below
        digest = w._digest()
        with self._lock:
            if not overwrite and key in self.blobs:
                raise ArtifactExists(key)
            self.blobs[key] = (buf.getvalue(), time.time())
        w.info = ArtifactInfo(key, len(self.blobs[key][0]), self.blobs[key][1], digest)

    def open(self, key: str) -> BinaryIO:
        validate_key(key)
        if key not in self.blobs:
            raise ArtifactNotFound(key)
        return io.BytesIO(self.blobs[key][0])

    def stat(self, key: str) -> ArtifactInfo:
        validate_key(key)
        if key not in self.blobs:
            raise ArtifactNotFound(key)
        data, mtime = self.blobs[key]
        return ArtifactInfo(key, len(data), mtime)

    def list(self, prefix: str = "") -> Iterator[ArtifactInfo]:
        if prefix:
            validate_key(prefix)
        keys = sorted(k for k in self.blobs if not prefix or k == prefix or k.startswith(prefix + "/"))
        return iter([self.stat(k) for k in keys])

    @contextlib.contextmanager
    def materialize(self, key: str) -> Iterator[Path]:
        data = self.open(key).read()
        tmp = Path(tempfile.mkdtemp(prefix="artifact-"))
        self.materialized_dirs.append(tmp)
        try:
            path = tmp / key.rsplit("/", 1)[-1]
            path.write_bytes(data)
            yield path
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def delete(self, key: str, *, missing_ok: bool = False) -> None:
        validate_key(key)
        if self.fail_delete:
            raise ArtifactStoreError("injected delete failure")
        if key not in self.blobs:
            if missing_ok:
                return
            raise ArtifactNotFound(key)
        del self.blobs[key]

    def delete_prefix(self, prefix: str) -> int:
        validate_key(prefix)
        doomed = [k for k in self.blobs if k == prefix or k.startswith(prefix + "/")]
        for k in doomed:
            self.delete(k)
        return len(doomed)

    def move(self, src: str, dst: str, *, overwrite: bool = False) -> ArtifactInfo:
        validate_key(src), validate_key(dst)
        if src not in self.blobs:
            raise ArtifactNotFound(src)
        if not overwrite and dst in self.blobs:
            raise ArtifactExists(dst)
        self.blobs[dst] = self.blobs[src]          # copy first: dst is complete before src goes (documented window)
        if src != dst:
            self.delete(src)
        return self.stat(dst)


class _FaultyBuffer(io.BytesIO):
    def __init__(self, fail_after: int | None):
        super().__init__()
        self._fail_after = fail_after

    def write(self, data) -> int:  # type: ignore[override]
        n = super().write(data)
        if self._fail_after is not None and self.tell() > self._fail_after:
            raise ArtifactStoreError("injected write failure")
        return n
