"""Core capability definitions (design spec §1). A plugin may *provide* any of these; core code consumes them through the
plugin host. Plugin-defined capabilities come from `PluginManifest.defines`, not from here."""
from __future__ import annotations

from .definition import CAP_ID_RE, CapabilityDef, RoutedCapability
from .filament_inventory import DEFINITION as _FILAMENT
from .gcode_produce import DEFINITION as _GCODE_PRODUCE
from .notify_channel import DEFINITION as _NOTIFY
from .printer_client import DEFINITION as _PRINTER_CLIENT
from .three_mf_construct import DEFINITION as _3MF_CONSTRUCT

CORE: dict[str, CapabilityDef] = {d.id: d for d in (_FILAMENT, _PRINTER_CLIENT, _NOTIFY, _GCODE_PRODUCE, _3MF_CONSTRUCT)}

__all__ = ["CAP_ID_RE", "CORE", "CapabilityDef", "RoutedCapability"]
