"""The core -> plugin channel for core-defined values (BIZ-289). A plugin never reads the process environment; anything the
environment decides (where Themis keeps its data) is resolved once by core and handed over here.

A manifest factory may take it as an optional second argument: `factory(settings)` or `factory(settings, context)`."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class PluginContext:
    plugin_id: str
    data_dir: Path             # this plugin's own directory under Themis's data dir (<data>/plugin-data/<id>); not created for it
    plugin_dir: Path | None    # where the plugin's package (themis-plugin.toml, defaults.toml) lives; None if unknown
    # Job events and details (docs/printer-interface.md). Core-built, bound to this plugin; None when a context is made without them (tests).
    job_reporter: Any = None          # `await report(job_id, "started"|"complete"|"failed"|"blocked", details=None, *, reason=None, printer_id=None)`: core applies and publishes; a plugin with several printers must pass `printer_id`
    job_details: Any = None           # read-only `await get(job_id, fields, origin=None)`
    job_details_writer: Any = None    # `await set(job_id, {field: value})`: this plugin's fields on a job


def wants_context(factory: Callable[..., Any]) -> bool:
    """True when the factory declares a second positional parameter for the context."""
    try:
        params = inspect.signature(factory).parameters.values()
    except (TypeError, ValueError):
        return False
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return len(positional) >= 2
