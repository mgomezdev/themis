"""Provider-neutral catalog source contract (BIZ-348).

Catalog identity is separate from a materialized UploadedFile. Consumers route calls
by the entry's provider ID; no source is a fallback for another source's opaque IDs.
See docs/superpowers/specs/2026-11-19-library-aggregator-manyfold-design.md.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .definition import CapabilityDef, RoutedCapability

CAPABILITY = "library.source"
BACKLINK = "BACKLINK"
BROWSE = "BROWSE"


@dataclass(frozen=True)
class SourceCreator:
    id: str
    name: str
    links: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceFile:
    source_file_id: str
    name: str
    source_url: str
    media_type: str | None = None
    size_bytes: int | None = None
    revision: str | None = None
    creator: SourceCreator | None = None
    license_code: str | None = None
    license_url: str | None = None
    collections: tuple[str, ...] = ()
    preview_url: str | None = None


@dataclass(frozen=True)
class CatalogPage:
    entries: tuple[SourceFile, ...]
    next_cursor: str | None = None
    # True only for pages from an authoritative full sweep. A consumer may mark
    # missing records only after EVERY page succeeds and next_cursor is None.
    full_scan: bool = False


@dataclass(frozen=True)
class SourceDownload:
    chunks: AsyncIterator[bytes]
    revision: str | None = None
    size_bytes: int | None = None
    media_type: str | None = None


@runtime_checkable
class LibrarySource(Protocol):
    async def test_connection(self) -> dict: ...

    async def list_catalog(self, cursor: str | None = None, changed_since: str | None = None) -> CatalogPage: ...

    async def describe(self, source_file_id: str) -> SourceFile: ...

    def download(self, source_file_id: str, revision: str | None = None) -> AbstractAsyncContextManager[SourceDownload]:
        """Open a bounded stream; consumers must close it on success, error and cancellation.

        Consume the context/iterator inside host.call_for's callback, not after its
        containment has returned. Core enforces byte limits and publishes atomically.
        """
        ...


@runtime_checkable
class BacklinkSource(Protocol):
    async def backlink(self, source_file_id: str, target_url: str, label: str) -> None:
        """Opt-in only; preserve unrelated source links. Never invoked by a read-only sync."""
        ...


@runtime_checkable
class BrowsableSource(Protocol):
    async def browse(self, query: str, cursor: str | None = None) -> CatalogPage: ...


DEFINITION = CapabilityDef(
    id=CAPABILITY, version=1, label="Library source", mode="routed", protocol=LibrarySource,
    features=frozenset({BACKLINK, BROWSE}),
    description="Catalogs of model files, downloaded into the local library when used.",
)
LIBRARY_SOURCE: RoutedCapability[LibrarySource] = RoutedCapability(CAPABILITY)
