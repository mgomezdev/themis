"""Plugin default configuration (BIZ-289): the plugin-owned `defaults.toml` next to `themis-plugin.toml`, and the
core-resolved inputs a plugin never reads itself.

Settings precedence, highest first (see docs/plugins.md "Configuration and defaults"):
  1. the plugin's saved settings (DB, edited on its Settings page; blank = unset),
  2. core override inputs: deprecated environment variables a BUNDLED plugin's `defaults.toml` maps in `[legacy_env]`.
     CORE reads them (`app.config.read_env`), logs a deprecation warning once, and hands the value over,
  3. the shipped defaults in `defaults.toml` (`[settings]`, and a top-level `enabled`),
  4. the settings model's own field defaults.

`defaults.toml` shape::

    enabled = false            # optional: replaces the manifest's default_enabled
    [settings]                 # shipped setting defaults (validated by the plugin's settings model)
    url = "http://orca:5000"
    [legacy_env]               # optional: setting (or "enabled") -> deprecated env var core reads on the plugin's behalf
    url = "LAMINUS_SIDECAR_URL"

No plugin module reads the process environment: this file is parsed here (host side) and the values are passed in.
"""
from __future__ import annotations

import functools
import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config

logger = logging.getLogger("app")

DEFAULTS_NAME = "defaults.toml"
_TRUE = {"1", "true", "yes", "on"}
_warned: set[tuple[str, str]] = set()


@dataclass(frozen=True)
class PluginDefaults:
    enabled: bool | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    legacy_env: dict[str, str] = field(default_factory=dict)      # key ("enabled" or a setting name) -> env var name


@dataclass(frozen=True)
class CoreOverrides:
    """Values core resolved from the environment for one plugin (deprecated inputs)."""
    enabled: bool | None = None
    settings: dict[str, Any] = field(default_factory=dict)


def load_defaults(root: Path | None) -> PluginDefaults:
    """Parse `<root>/defaults.toml`; a missing file is no defaults, a broken one is logged and ignored (Themis must boot)."""
    if root is None:
        return PluginDefaults()
    path = root / DEFAULTS_NAME
    if not path.is_file():
        return PluginDefaults()
    try:
        if path.stat().st_size > 16_384:
            raise ValueError("file too large")
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        enabled = data.get("enabled")
        settings, legacy = data.get("settings", {}), data.get("legacy_env", {})
        if (enabled is not None and not isinstance(enabled, bool)) or not isinstance(settings, dict) \
                or not isinstance(legacy, dict) or not all(isinstance(v, str) for v in legacy.values()):
            raise ValueError("expected `enabled` (bool), [settings] and [legacy_env] (name -> env var) tables")
        return PluginDefaults(enabled, dict(settings), dict(legacy))
    except Exception as e:
        logger.warning("Ignoring unreadable %s for plugin at %s: %s", DEFAULTS_NAME, root, e)
        return PluginDefaults()


def defaults_of(plugin_id: str) -> PluginDefaults:
    """The shipped defaults of a registered plugin."""
    from . import plugin_root
    return load_defaults(plugin_root(plugin_id))


@functools.cache
def _bundled() -> frozenset[str]:
    from . import bundled_ids
    return bundled_ids()


def is_bundled(plugin_id: str) -> bool:
    return plugin_id in _bundled()


def core_overrides_of(plugin_id: str) -> CoreOverrides:
    """Deprecated env inputs for the plugin, read through core at call time (cheap) and warned about once per process.
    Only BUNDLED plugins may map env vars: an installed plugin's `[legacy_env]` is ignored, so third-party code can never
    make core read (and hand it) an arbitrary environment variable such as a secret."""
    legacy = defaults_of(plugin_id).legacy_env if is_bundled(plugin_id) else {}
    enabled: bool | None = None
    settings: dict[str, Any] = {}
    for key, var in legacy.items():
        raw = config.read_env(var)
        if raw is None or not raw.strip():
            continue
        if (plugin_id, var) not in _warned:
            _warned.add((plugin_id, var))
            logger.warning("Environment variable %s is deprecated (plugin %s): set it in the plugin's Settings instead. "
                           "Its value is still honoured as a default until a setting is saved.", var, plugin_id)
        if key == "enabled":
            enabled = raw.strip().lower() in _TRUE
        else:
            settings[key] = raw.strip()
    return CoreOverrides(enabled, settings)
