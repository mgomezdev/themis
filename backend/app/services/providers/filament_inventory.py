"""Filament inventory provider interface (async).

Mirrors what `spoolman_service` does today. Refs (`Filament.ref`, `Spool.ref`) are opaque provider
strings; persisted ids elsewhere in Themis (`loaded_filaments[].spoolman_spool_id`, low-stock overrides)
stay as they are and are converted at the call site.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar


class InventoryProviderError(Exception):
    """Neutral failure from an inventory provider.

    `code` is a short machine-readable kind (an HTTP status string like "503", or an exception class
    name for transport errors); `status` is the upstream HTTP status when there was one."""

    def __init__(self, message: str, code: str = "error", status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class Filament:
    ref: str
    name: str = ""
    vendor: str | None = None
    material: str | None = None
    color_hex: str | None = None
    # {printer_preset: [filament_profile_name, ...]}; empty when the provider has none / lacks PROFILE_BINDINGS.
    profile_bindings: dict[str, list[str]] = field(default_factory=dict)
    # Vendor payload, kept so legacy routes can re-serialize the exact shape they always returned.
    raw: dict = field(default_factory=dict)


@dataclass
class Spool:
    ref: str
    filament_ref: str | None = None
    filament_name: str = ""
    remaining_weight: float | None = None
    archived: bool = False
    raw: dict = field(default_factory=dict)


class FilamentInventoryProvider(ABC):
    TRACKS_WEIGHT: ClassVar[bool] = False
    RECORDS_USAGE: ClassVar[bool] = False
    PROFILE_BINDINGS: ClassVar[bool] = False

    @abstractmethod
    async def test_connection(self) -> dict: ...

    @abstractmethod
    async def list_filaments(self) -> list[Filament]: ...

    @abstractmethod
    async def get_filament(self, ref: str) -> Filament: ...

    @abstractmethod
    async def list_spools(self) -> list[Spool]: ...

    @abstractmethod
    async def record_usage(self, spool_ref: str, grams: float) -> None: ...

    @abstractmethod
    async def get_profile_bindings(self, filament_ref: str) -> dict[str, list[str]]: ...

    @abstractmethod
    async def set_profile_bindings(self, filament_ref: str, bindings: dict[str, list[str]]) -> Filament:
        """Replace the filament's bindings; returns the updated filament."""


# name -> adapter class. Adding a provider = one adapter class + one entry here.
_REGISTRY: dict[str, type[FilamentInventoryProvider]] = {}


def register_inventory_provider(name: str, cls: type[FilamentInventoryProvider]) -> None:
    _REGISTRY[name] = cls


async def get_inventory_provider(session: Any) -> FilamentInventoryProvider | None:
    """The configured inventory provider, or None when the integration is missing/disabled/unconfigured.

    Replaces the per-caller `SpoolmanConfig` enabled/url checks."""
    from . import spoolman  # noqa: F401  (registers the adapter)
    from ...models import SpoolmanConfig

    row = await session.get(SpoolmanConfig, 1)
    if row is None or not row.enabled or not row.url:
        return None
    return _REGISTRY["spoolman"](row.url, row.api_key)
