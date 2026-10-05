"""Plugin registry: a plain dict filled at startup from bundled plugins (and, later, installed ones).

Core never imports a specific plugin; it asks the host (`plugins.host.plugin_host`) for the active provider of a
kind. See docs/agent/backend.md and the design spec (Linear, BIZ-202)."""
from __future__ import annotations

import importlib

from .manifest import HOST_API, PluginError, PluginManifest, UiContribution, UiTab

__all__ = ["HOST_API", "PluginError", "PluginManifest", "UiContribution", "UiTab", "register_plugin", "get_plugin",
           "plugins_of_kind", "registered_plugins", "load_bundled", "BUNDLED_MODULES"]

def _discover_bundled() -> tuple[str, ...]:
    """Bundled plugins are the sub-packages of this one that export `MANIFEST` (everything but `kinds`, the contracts). They
    ship in the image and cannot be uninstalled. Discovery by directory keeps this module from naming any plugin."""
    import pkgutil
    return tuple(f"{__name__}.{m.name}" for m in pkgutil.iter_modules(__path__)
                 if m.ispkg and not m.name.startswith("_") and m.name != "kinds")


# Modules (dotted paths) that export `MANIFEST`.
BUNDLED_MODULES: tuple[str, ...] = _discover_bundled()

_REGISTRY: dict[str, PluginManifest] = {}


def register_plugin(manifest: PluginManifest) -> None:
    existing = _REGISTRY.get(manifest.id)
    if existing is not None and existing is not manifest:
        raise PluginError(f"plugin id {manifest.id!r} is already registered")
    _REGISTRY[manifest.id] = manifest


def get_plugin(plugin_id: str) -> PluginManifest | None:
    return _REGISTRY.get(plugin_id)


def plugins_of_kind(kind: str) -> list[PluginManifest]:
    return sorted((m for m in _REGISTRY.values() if m.kind == kind), key=lambda m: m.id)


def registered_plugins() -> list[PluginManifest]:
    return sorted(_REGISTRY.values(), key=lambda m: m.id)


def load_bundled() -> list[str]:
    """Import and register every bundled plugin. A broken one is logged and skipped (Themis must boot); returns the
    ids of the plugins that failed so callers/tests can see them."""
    import logging
    failed: list[str] = []
    for dotted in BUNDLED_MODULES:
        try:
            register_plugin(importlib.import_module(dotted).MANIFEST)
        except Exception:
            logging.getLogger("app").exception("Bundled plugin %s failed to load", dotted)
            failed.append(dotted)
    return failed
