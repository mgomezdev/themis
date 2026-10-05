"""The `filament_inventory` plugin kind (design spec §3.2): neutral DTOs + the provider ABC.

Core and the frontend only ever see these DTOs, never a provider's raw JSON, and branch only on **capabilities**
(never on a plugin id). There is deliberately no "record usage / subtract N grams" method (decision D6): core sends the
absolute weight it computed (`set_remaining`), so re-sending is harmless for every provider.

`raw` on the DTOs is provider-private (kept so the provider's own alias routes can relay its exact payloads); core
code must not read it and the neutral API never serialises it."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

KIND = "filament_inventory"

TRACKS_WEIGHT = "TRACKS_WEIGHT"            # spools report a remaining weight
WRITE_WEIGHT = "WRITE_WEIGHT"              # set_remaining() works
PROFILE_LINKS_READ = "PROFILE_LINKS_READ"  # materials carry {orca printer preset: [orca filament presets]}
PROFILE_LINKS_WRITE = "PROFILE_LINKS_WRITE"
LABEL_SCAN = "LABEL_SCAN"                  # parse_label() understands the provider's QR/label text
REMOTE = "REMOTE"                          # lives elsewhere and can be unreachable (cache, outbox, disconnect alert)
ALL_CAPABILITIES = frozenset({TRACKS_WEIGHT, WRITE_WEIGHT, PROFILE_LINKS_READ, PROFILE_LINKS_WRITE, LABEL_SCAN, REMOTE})


class InventoryProviderError(Exception):
    """Neutral failure from a provider. `code` is a short machine-readable kind (an HTTP status string like "503", or
    an exception class name for transport errors); `status` is the upstream HTTP status when there was one."""

    def __init__(self, message: str, code: str = "error", status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class NotSupported(Exception):
    """The provider lacks the capability an optional method needs (callers should have checked `capabilities`)."""

    def __init__(self, capability: str) -> None:
        super().__init__(f"the provider does not support {capability}")
        self.capability = capability


@dataclass
class InvMaterial:
    ref: str                                 # provider-opaque id, always a string
    name: str = ""
    material: str | None = None              # "PLA" — compared against type asks
    color_hex: str | None = None             # "#RRGGBB"
    vendor: str | None = None
    density: float | None = None
    diameter: float | None = None
    profile_links: dict[str, list[str]] | None = None   # None = the provider has none / lacks PROFILE_LINKS_READ
    raw: dict = field(default_factory=dict, repr=False, compare=False)


@dataclass
class InvSpool:
    ref: str
    material_ref: str | None = None
    material: InvMaterial | None = None
    remaining_g: float | None = None         # None = the provider doesn't track weight
    location: str | None = None
    label: str = ""
    archived: bool = False
    raw: dict = field(default_factory=dict, repr=False, compare=False)


class FilamentInventoryProvider(ABC):
    capabilities: ClassVar[frozenset[str]] = frozenset()

    @abstractmethod
    async def test_connection(self) -> dict: ...

    @abstractmethod
    async def list_materials(self) -> list[InvMaterial]: ...

    @abstractmethod
    async def list_spools(self) -> list[InvSpool]: ...

    @abstractmethod
    async def get_spool(self, spool_ref: str) -> InvSpool | None:
        """One spool, or None when it does not exist (the pre-print snapshot and the interim deduction read it)."""

    # --- optional, gated by capability ------------------------------------------------------------------------

    async def set_remaining(self, spool_ref: str, remaining_g: float) -> None:            # WRITE_WEIGHT
        raise NotSupported(WRITE_WEIGHT)

    async def set_profile_links(self, material_ref: str, links: dict[str, list[str]]) -> InvMaterial:   # PROFILE_LINKS_WRITE
        raise NotSupported(PROFILE_LINKS_WRITE)

    def parse_label(self, text: str) -> str | None:                                         # LABEL_SCAN -> spool ref
        raise NotSupported(LABEL_SCAN)

    def spool_url(self, ref: str) -> str | None:
        """A deep link into the provider's own UI, if it has one."""
        return None
