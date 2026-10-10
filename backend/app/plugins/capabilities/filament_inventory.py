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

from .definition import CapabilityDef

CAPABILITY = "inventory.filament"

TRACKS_WEIGHT = "TRACKS_WEIGHT"            # spools report a remaining weight
WRITE_WEIGHT = "WRITE_WEIGHT"              # set_remaining() works
PROFILE_LINKS_READ = "PROFILE_LINKS_READ"  # materials carry {orca printer preset: [orca filament presets]}
PROFILE_LINKS_WRITE = "PROFILE_LINKS_WRITE"
LABEL_SCAN = "LABEL_SCAN"                  # parse_label() understands the provider's QR/label text
REMOTE = "REMOTE"                          # lives elsewhere and can be unreachable (cache, outbox, disconnect alert)
MANAGE_MATERIALS = "MANAGE_MATERIALS"      # create/update/archive materials (a provider that owns its own library)
MANAGE_SPOOLS = "MANAGE_SPOOLS"            # create/update/archive spools
ALL_CAPABILITIES = frozenset({TRACKS_WEIGHT, WRITE_WEIGHT, PROFILE_LINKS_READ, PROFILE_LINKS_WRITE, LABEL_SCAN, REMOTE,
                              MANAGE_MATERIALS, MANAGE_SPOOLS})

DEFINITION = CapabilityDef(
    id=CAPABILITY, version=1, label="Filament inventory",
    description="Where Themis looks up spools and materials, and keeps their weights up to date.",
    features=ALL_CAPABILITIES)

# Machine-readable contract (BIZ-247): every optional provider method and the one capability flag that gates it. A provider that
# claims a flag MUST override each method listed for it (the base class raises `NotSupported(flag)`); one that doesn't claim the
# flag leaves the method alone and core never calls it (it checks `host.has(CAPABILITY, flag)` first). `spool_url` is optional
# but ungated: the base returns None. Anything not in this map and not abstract is not part of the contract.
OPTIONAL_METHODS: dict[str, str] = {
    "set_remaining": WRITE_WEIGHT,
    "set_profile_links": PROFILE_LINKS_WRITE,
    "create_material": MANAGE_MATERIALS,
    "update_material": MANAGE_MATERIALS,
    "archive_material": MANAGE_MATERIALS,
    "create_spool": MANAGE_SPOOLS,
    "update_spool": MANAGE_SPOOLS,
    "archive_spool": MANAGE_SPOOLS,
    "parse_label": LABEL_SCAN,
}
# Flags that gate *data*, not a method: they promise what list_*/get_spool return (weights; profile links), or how core treats the
# provider (REMOTE: cache, outbox, disconnect alert).
DATA_FLAGS = frozenset({TRACKS_WEIGHT, PROFILE_LINKS_READ, REMOTE})


def contract_violations(provider: object) -> list[str]:
    """Why `provider` (an instance, or a class whose `capabilities` is class-level) breaks the capability/method contract (empty =
    it honours it). Nothing is called, only inspected:
    * a claimed flag whose gated method is not overridden (the base would raise NotSupported while the flag says it works);
    * a claimed flag that is not a known flag;
    * a method overridden while its flag is NOT claimed (core would never call it: dead code that hides a missing flag)."""
    cls = provider if isinstance(provider, type) else type(provider)
    claimed = set(getattr(provider, "capabilities", frozenset()))
    out = [f"claims unknown capability flag {f!r}" for f in sorted(claimed - ALL_CAPABILITIES)]
    for method, flag in OPTIONAL_METHODS.items():
        overridden = getattr(cls, method, None) is not getattr(FilamentInventoryProvider, method)
        if flag in claimed and not overridden:
            out.append(f"claims {flag} but does not implement {method}()")
        if overridden and flag not in claimed:
            out.append(f"implements {method}() but does not claim {flag}")
    return out


MATERIAL_FIELDS = ("name", "material", "color_hex", "vendor", "density", "diameter")     # what create/update_material accept
SPOOL_FIELDS = ("label", "location")                                                    # what update_spool accepts (weight: set_remaining)


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
    archived: bool = False
    raw: dict = field(default_factory=dict, repr=False, compare=False)


@dataclass
class InvSpool:
    ref: str
    material_ref: str | None = None
    material: InvMaterial | None = None
    remaining_g: float | None = None         # None = the provider doesn't track weight
    initial_g: float | None = None           # weight when new (percent remaining = remaining_g / initial_g); None = unknown
    location: str | None = None
    label: str = ""
    archived: bool = False
    raw: dict = field(default_factory=dict, repr=False, compare=False)


@dataclass
class MaterialDraft:
    """What `create_material` needs (`name` is the only required field)."""
    name: str
    material: str | None = None
    color_hex: str | None = None
    vendor: str | None = None
    density: float | None = None
    diameter: float | None = None


@dataclass
class SpoolDraft:
    """What `create_spool` needs. Weights are grams; `remaining_g` defaults to `initial_g` when only that is given."""
    material_ref: str
    label: str | None = None
    location: str | None = None
    initial_g: float | None = None
    remaining_g: float | None = None


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

    # Library management — only for providers that own their library (capability-gated, never by plugin id). A provider
    # without these (its library lives in the external system) is still a valid provider. Failures use
    # InventoryProviderError (status 404 unknown ref, 422 invalid input).
    #
    # Semantics every provider follows (the UI relies on them):
    #  * A patch value of None CLEARS an optional field (vendor, material, color_hex, density, diameter, location); `name`
    #    and a spool `label` can never be cleared (422). Keys absent from the patch are untouched.
    #  * Archiving is reversible and never cascades: archiving a material leaves its spools as they are, and archived
    #    materials/spools are still returned by list_*/get_spool (flagged `archived`; core hides them by default).
    #    Archived items can still be edited and weighed. create_spool on an ARCHIVED material is a 422.
    #  * create_spool: `remaining_g` defaults to `initial_g`; `remaining_g > initial_g` is a 422. `initial_g` is create-only
    #    input that surfaces as InvSpool.initial_g.
    #  * list_* return a stable order (creation order); core re-sorts by ref so the API order is deterministic.
    async def create_material(self, draft: MaterialDraft) -> InvMaterial:                   # MANAGE_MATERIALS
        raise NotSupported(MANAGE_MATERIALS)

    async def update_material(self, ref: str, patch: dict) -> InvMaterial:                  # MANAGE_MATERIALS; keys in MATERIAL_FIELDS
        raise NotSupported(MANAGE_MATERIALS)

    async def archive_material(self, ref: str, archived: bool = True) -> InvMaterial:       # MANAGE_MATERIALS
        raise NotSupported(MANAGE_MATERIALS)

    async def create_spool(self, draft: SpoolDraft) -> InvSpool:                            # MANAGE_SPOOLS
        raise NotSupported(MANAGE_SPOOLS)

    async def update_spool(self, ref: str, patch: dict) -> InvSpool:                        # MANAGE_SPOOLS; keys in SPOOL_FIELDS
        raise NotSupported(MANAGE_SPOOLS)

    async def archive_spool(self, ref: str, archived: bool = True) -> InvSpool:             # MANAGE_SPOOLS
        raise NotSupported(MANAGE_SPOOLS)

    def parse_label(self, text: str) -> str | None:                                         # LABEL_SCAN -> spool ref
        raise NotSupported(LABEL_SCAN)

    def spool_url(self, ref: str) -> str | None:
        """A deep link into the provider's own UI, if it has one."""
        return None
