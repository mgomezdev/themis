"""Core capability definitions (design spec §1). A plugin may *provide* any of these; core code consumes them through the
plugin host. Plugin-defined capabilities come from `PluginManifest.defines`, not from here."""
from __future__ import annotations

from .definition import CAP_ID_RE, CapabilityDef
from .filament_inventory import DEFINITION as _FILAMENT

CORE: dict[str, CapabilityDef] = {d.id: d for d in (_FILAMENT,)}

__all__ = ["CAP_ID_RE", "CORE", "CapabilityDef"]
