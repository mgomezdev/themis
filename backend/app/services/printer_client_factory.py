"""Resolve a printer's client class through the plugin registry (BIZ-251). Core names no vendor: a printer plugin exposes
its client class as its manifest `factory`, and a printer row points at its plugin by `plugin_id`."""
from __future__ import annotations

import inspect
from dataclasses import asdict

from ..models import Printer
from ..plugins import get_plugin, registered_plugins
from ..plugins.host import plugin_host
from ..plugins.manifest import PluginManifest
from .abstract_printer_client import AbstractPrinterClient
from .printer_identity import LEGACY_IDENTITY


def _is_client_class(obj) -> bool:
    return isinstance(obj, type) and issubclass(obj, AbstractPrinterClient)


def printer_client_plugins(*, enabled_only: bool = False) -> list[tuple[PluginManifest, type[AbstractPrinterClient]]]:
    """Every registered plugin that serves printers (its factory is a client class), optionally only the enabled ones."""
    return [(m, m.factory) for m in registered_plugins()
            if _is_client_class(m.factory) and (not enabled_only or plugin_host.is_enabled(m.id))]


def client_class(key: str | None) -> type[AbstractPrinterClient] | None:
    """The client class for a plugin id or a legacy `printer_type` (old rows and backups carry that); None if no such plugin."""
    if not key:
        return None
    manifest = get_plugin(key)
    if manifest is None and key in LEGACY_IDENTITY:
        manifest = get_plugin(LEGACY_IDENTITY[key][0])
    return manifest.factory if manifest is not None and _is_client_class(manifest.factory) else None


def _get_class(key: str | None) -> type[AbstractPrinterClient]:
    cls = client_class(key)
    if cls is None:
        raise ValueError(f"Unknown printer type: {key!r}")
    return cls


def enabled_client_classes() -> dict[str, type[AbstractPrinterClient]]:
    """Client classes of the enabled printer plugins by their `printer_type` (what discovery sweeps)."""
    return {cls.printer_type: cls for _, cls in printer_client_plugins(enabled_only=True)}


def printer_type_plugins() -> dict[str, str]:
    """Client `printer_type` -> the id of the plugin that serves it."""
    return {cls.printer_type: m.id for m, cls in printer_client_plugins()}


def printer_type_names() -> dict[str, str]:
    """Client `printer_type` -> its plugin's display name."""
    return {cls.printer_type: m.name for m, cls in printer_client_plugins()}


def _accepted_kwargs(cls: type[AbstractPrinterClient], config: dict) -> dict:
    accepted = {f.name for f in cls.connection_fields()}
    return {k: v for k, v in config.items() if k in accepted}


def create_client(printer: Printer, **callbacks) -> AbstractPrinterClient:
    cls = _get_class(printer.plugin_id or printer.printer_type)
    kwargs = _accepted_kwargs(cls, printer.connection_config or {})
    sig = inspect.signature(cls.__init__)
    for k, v in callbacks.items():
        if k in sig.parameters:
            kwargs[k] = v
    return cls(**kwargs)


def create_client_from_config(plugin_or_type: str, connection_config: dict) -> AbstractPrinterClient:
    cls = _get_class(plugin_or_type)
    return cls(**_accepted_kwargs(cls, connection_config))
