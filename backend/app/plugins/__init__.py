"""Plugin registry: a plain dict filled at startup from bundled plugins (and, later, installed ones).

Core never imports a specific plugin; it asks the host (`plugins.host.plugin_host`) for the active provider of a
capability. See docs/agent/backend.md and the design spec (Linear, BIZ-202)."""
from __future__ import annotations

import importlib
import importlib.util
import logging
from pathlib import Path

from .capabilities.definition import CapabilityDef
from .manifest import HOST_API, JobFieldDef, PluginError, PluginManifest, Provide, Requirement, UiContribution, UiTab
from .ui_schema import UiReport, inspect_manifest

__all__ = ["HOST_API", "JobFieldDef", "PluginError", "PluginManifest", "Provide", "Requirement", "CapabilityDef", "UiContribution", "UiTab",
           "register_plugin", "ui_report", "plugin_root", "get_plugin", "providers_of", "capability_catalog", "definer_of", "registered_plugins", "load_bundled", "bundled_ids", "BUNDLED_MODULES"]

def _discover_bundled() -> tuple[str, ...]:
    """Bundled plugins are the sub-packages of this one that export `MANIFEST` (everything but `capabilities`, the contracts). They
    ship in the image and cannot be uninstalled. Discovery by directory keeps this module from naming any plugin."""
    import pkgutil
    return tuple(f"{__name__}.{m.name}" for m in pkgutil.iter_modules(__path__)
                 if m.ispkg and not m.name.startswith("_") and m.name != "capabilities")


# Modules (dotted paths) that export `MANIFEST`.
BUNDLED_MODULES: tuple[str, ...] = _discover_bundled()

_REGISTRY: dict[str, PluginManifest] = {}
_ROOTS: dict[str, Path] = {}                 # plugin id -> its package directory (where themis-plugin.toml / defaults.toml live)
_UI_REPORTS: dict[str, UiReport] = {}      # plugin id -> the declarative-UI validation done when it registered (BIZ-303)


def register_plugin(manifest: PluginManifest, root: Path | None = None) -> None:
    existing = _REGISTRY.get(manifest.id)
    if existing is not None and existing is not manifest:
        raise PluginError(f"plugin id {manifest.id!r} is already registered")
    _REGISTRY[manifest.id] = manifest
    if root is not None:
        _ROOTS[manifest.id] = root
    _UI_REPORTS[manifest.id] = inspect_manifest(manifest)      # never raises: a bad contribution is a diagnostic, not a boot failure
    _warn_missing_job_fields(manifest)


def _warn_missing_job_fields(manifest: PluginManifest) -> None:
    """A printer plugin (one that declares printer models or provides `printer.client`) should declare, or mark unsupported, the
    minimum job fields (docs/printer-interface.md). A gap is logged at load and never blocks the plugin."""
    if not (manifest.manufacturers or "printer.client" in manifest.provides):
        return
    from .job_fields import missing_min_fields
    missing = missing_min_fields(manifest)
    if missing:
        logging.getLogger("app").warning("Printer plugin %s neither declares nor marks unsupported the minimum job field(s) %s",
                                         manifest.id, ", ".join(missing))


def ui_report(plugin_id: str) -> UiReport:
    """What validating the plugin's declarative UI found when it registered (empty for an unknown plugin)."""
    return _UI_REPORTS.get(plugin_id) or UiReport()


def plugin_root(plugin_id: str) -> Path | None:
    return _ROOTS.get(plugin_id)


def get_plugin(plugin_id: str) -> PluginManifest | None:
    return _REGISTRY.get(plugin_id)


def providers_of(cap_id: str) -> list[PluginManifest]:
    return sorted((m for m in _REGISTRY.values() if cap_id in m.provides), key=lambda m: m.id)


def capability_catalog() -> dict[str, CapabilityDef]:
    """Core definitions plus the `defines` of every registered plugin."""
    from .capabilities import CORE
    out = dict(CORE)
    for m in _REGISTRY.values():
        for d in m.defines:
            out[d.id] = d
    return out


def definer_of(cap_id: str) -> str | None:
    """The plugin that defines `cap_id`, or None for a core (or unknown) capability."""
    return next((m.id for m in _REGISTRY.values() if any(d.id == cap_id for d in m.defines)), None)


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
            register_plugin(module.MANIFEST, Path(module.__file__).parent)
        except Exception:
            logging.getLogger("app").exception("Bundled plugin %s failed to load", dotted)
            failed.append(dotted)
    return failed
