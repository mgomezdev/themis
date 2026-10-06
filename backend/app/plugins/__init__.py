"""Plugin registry: a plain dict filled at startup from bundled plugins (and, later, installed ones).

Core never imports a specific plugin; it asks the host (`plugins.host.plugin_host`) for the active provider of a
kind. See docs/agent/backend.md and the design spec (Linear, BIZ-202)."""
from __future__ import annotations

import importlib
import importlib.util

from .manifest import HOST_API, PluginError, PluginManifest, UiContribution, UiTab

__all__ = ["HOST_API", "PluginError", "PluginManifest", "UiContribution", "UiTab", "register_plugin", "get_plugin",
           "plugins_of_kind", "registered_plugins", "load_bundled", "bundled_ids", "BUNDLED_MODULES"]

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


def bundled_ids() -> frozenset[str]:
    """Ids of the bundled plugins, read from their `themis-plugin.toml` (an installed plugin may not claim one)."""
    from pathlib import Path
    from .package import TOML_NAME, parse_toml
    ids: set[str] = set()
    for dotted in BUNDLED_MODULES:
        path = Path(importlib.util.find_spec(dotted).origin).parent / TOML_NAME      # type: ignore[union-attr]
        try:
            ids.add(parse_toml(path.read_text(encoding="utf-8")).id)
        except Exception:
            ids.add(dotted.rsplit(".", 1)[-1])                    # a broken toml still reserves the directory's name
    return frozenset(ids)


def load_bundled() -> list[str]:
    """Import and register every bundled plugin, checking its `themis-plugin.toml` against the exported MANIFEST. A broken
    one is logged and skipped (Themis must boot); returns the ids of the plugins that failed so callers/tests can see them."""
    import logging
    from pathlib import Path
    from .package import TOML_NAME, check_matches, read_toml
    failed: list[str] = []
    for dotted in BUNDLED_MODULES:
        try:
            module = importlib.import_module(dotted)
            check_matches(read_toml(Path(module.__file__).parent), module.MANIFEST)
            register_plugin(module.MANIFEST)
        except Exception:
            logging.getLogger("app").exception("Bundled plugin %s failed to load", dotted)
            failed.append(dotted)
    return failed
