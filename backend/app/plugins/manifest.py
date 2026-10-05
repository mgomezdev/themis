"""Plugin manifest (design spec §3.1, §3.7). A plugin is an in-process package exporting a `PluginManifest`;
bundled plugins and (later) installed ones use the same shape. `host_api` is a public contract: the host refuses a
plugin that targets a different version."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Callable, Literal

from fastapi import APIRouter
from pydantic import BaseModel

HOST_API = 1
ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
RENDERERS = ("default", "schema", "component")


class PluginError(Exception):
    """A plugin is malformed or incompatible with this host."""


@dataclass(frozen=True)
class UiTab:
    id: str
    label: str
    # default   -> the shared default plugin page; schema -> a generic form/table from a schema the plugin serves at
    # GET /api/v1/plugins/{id}/ui/{tab_id}; component -> a React component compiled into Themis (bundled plugins only)
    renderer: Literal["default", "schema", "component"] = "default"


@dataclass(frozen=True)
class UiContribution:
    # "section": a collapsible section on Settings -> Plugins; "page": its own sidebar entry with tabs.
    mode: Literal["section", "page"] = "section"
    nav_label: str | None = None
    nav_placement: Literal["settings", "main"] = "settings"
    nav_icon: str | None = None
    tabs: tuple[UiTab, ...] = ()


@dataclass(frozen=True)
class PluginManifest:
    id: str                                   # stable, persisted, never renamed
    name: str
    kind: str                                 # e.g. "filament_inventory"
    version: str
    host_api: int
    settings_model: type[BaseModel]           # validation + generated settings form (JSON schema)
    factory: Callable[[BaseModel], Any]       # settings -> provider instance
    capabilities: frozenset[str] = frozenset()
    secret_fields: frozenset[str] = frozenset()   # write-only; never returned by any API
    ui: UiContribution = field(default_factory=UiContribution)
    routers: tuple[APIRouter, ...] = ()       # mounted under /api/v1/plugins/{id}/...
    migrations: tuple[ModuleType, ...] = ()   # plugin-owned tables, see plugins/migrations.py
    table_prefix: str | None = None           # plugin-owned tables must start with this (default "<id>_")
    description: str = ""
    docs_url: str | None = None
    permissions: tuple[str, ...] = ()         # reserved (BIZ-200): parsed, never enforced

    def __post_init__(self) -> None:
        if not ID_RE.match(self.id):
            raise PluginError(f"plugin id {self.id!r} must match {ID_RE.pattern}")
        if self.host_api != HOST_API:
            raise PluginError(f"plugin {self.id!r} targets host_api {self.host_api}; this Themis provides {HOST_API}")
        if not callable(self.factory):
            raise PluginError(f"plugin {self.id!r}: factory must be callable")
        unknown = self.secret_fields - set(self.settings_model.model_fields)
        if unknown:
            raise PluginError(f"plugin {self.id!r}: secret_fields {sorted(unknown)} are not in its settings model")
        for tab in self.ui.tabs:
            if tab.renderer not in RENDERERS:
                raise PluginError(f"plugin {self.id!r}: tab {tab.id!r} has unknown renderer {tab.renderer!r}")
        for mod in self.migrations:
            for attr in ("version", "name", "up", "down"):
                if not hasattr(mod, attr):
                    raise PluginError(f"plugin {self.id!r}: migration {mod.__name__} lacks `{attr}` (down() is required)")

    @property
    def prefix(self) -> str:
        return self.table_prefix or f"{self.id}_"
