"""Printer identity (BIZ-251/262): which plugin serves a printer and which manufacturer/model it is, validated against the
plugin manifests. Core names no plugin: the legacy table below is only the mapping for old `printer_type` values."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..plugins import get_plugin, registered_plugins
from ..plugins.host import plugin_host
from ..plugins.manifest import PrinterModel, PluginManifest

# legacy printer_type -> (plugin_id, manufacturer_id, model_id); the same mapping v042 backfilled.
LEGACY_IDENTITY: dict[str, tuple[str, str, str]] = {
    "bambu": ("bambu", "bambu", "p1s"),
    "elegoo_centauri": ("elegoo_centauri", "elegoo", "centauri"),
    "snapmaker_extended": ("snapmaker", "snapmaker", "u1_extended"),
    "mock": ("mock", "mock", "mock"),
}


class IdentityError(ValueError):
    """The requested plugin/manufacturer/model is not one the registered plugins declare."""


def resolve_legacy(printer_type: str) -> tuple[str, str, str]:
    if printer_type not in LEGACY_IDENTITY:
        raise IdentityError(f"Unknown printer_type: {printer_type!r}")
    return LEGACY_IDENTITY[printer_type]


def declared_model(plugin_id: str, manufacturer_id: str, model_id: str) -> tuple[PluginManifest, PrinterModel]:
    manifest = get_plugin(plugin_id)
    if manifest is None:
        raise IdentityError(f"Unknown plugin: {plugin_id!r}")
    for mfr in manifest.manufacturers:
        if mfr.id == manufacturer_id:
            for model in mfr.models:
                if model.id == model_id:
                    return manifest, model
    raise IdentityError(f"{plugin_id!r} does not declare {manufacturer_id}/{model_id}")


def dormant_reason(plugin_id: str | None) -> str | None:
    """Why a printer bound to `plugin_id` cannot be used right now, or None. Rows are never changed by this."""
    if plugin_id is None:
        return None
    if get_plugin(plugin_id) is None:
        return "plugin_removed"
    if not plugin_host.is_enabled(plugin_id):
        return "plugin_disabled"
    return None


def _connection_fields(manifest: PluginManifest) -> list[dict[str, Any]]:
    factory_fields = getattr(manifest.factory, "connection_fields", None)
    if not callable(factory_fields):
        return []
    return [asdict(f) for f in factory_fields()]


def printer_model_catalog() -> list[dict]:
    """Every model every registered plugin declares, enabled or not (the UI greys out disabled plugins)."""
    out: list[dict] = []
    for manifest in registered_plugins():
        fields = _connection_fields(manifest)
        enabled = plugin_host.is_enabled(manifest.id)
        for mfr in manifest.manufacturers:
            for model in mfr.models:
                out.append({
                    "plugin_id": manifest.id,
                    "manufacturer_id": mfr.id,
                    "manufacturer_name": mfr.name,
                    "model_id": model.id,
                    "display_name": model.name,
                    "bed_mm": list(model.bed_mm),
                    "toolheads": model.toolheads,
                    "connection_fields": fields,
                    "plugin_enabled": enabled,
                })
    return sorted(out, key=lambda e: (e["manufacturer_name"], e["display_name"]))
