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

from .capabilities import CORE
from .capabilities.definition import CAP_ID_RE, CapabilityDef

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
    # component tabs only: the key of the compiled-in React component (frontend `plugins/registry.ts`) and the capability
    # feature flag the plugin must declare for the tab to exist. Core names no plugin id; the plugin declares both.
    component: str | None = None
    requires: str | None = None


@dataclass(frozen=True)
class UiContribution:
    # "section": a collapsible section on Settings -> Plugins; "page": its own sidebar entry with tabs.
    mode: Literal["section", "page"] = "section"
    nav_label: str | None = None
    nav_placement: Literal["settings", "main"] = "settings"
    nav_icon: str | None = None
    tabs: tuple[UiTab, ...] = ()
    # (old absolute app URL, tab id): the frontend redirects the old URL to /plugins/{id}/{tab}
    redirects: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Provide:
    """How a plugin serves one capability. `attr` names the attribute of the plugin's instance that serves it (None = the
    instance itself); `features` are the capability's feature flags this provider supports; `routers` are mounted under
    /api/v1/plugins/{id}/... AND dispatched at /api/v1/capabilities/{cap}/... to whichever plugin is active."""
    version: int = 1
    attr: str | None = None
    features: frozenset[str] = frozenset()
    routers: tuple[APIRouter, ...] = ()


@dataclass(frozen=True)
class Requirement:
    capability: str
    min_version: int = 1

    @classmethod
    def parse(cls, text: str) -> "Requirement":
        cap, _, ver = text.partition("@")
        if not CAP_ID_RE.match(cap):
            raise PluginError(f"{text!r}: not a capability id")
        if "@" in text:
            if not ver.isdigit() or int(ver) < 1:
                raise PluginError(f"{text!r}: the minimum version after '@' must be a positive integer")
            return cls(cap, int(ver))
        return cls(cap, 1)


@dataclass(frozen=True)
class PluginManifest:
    id: str                                   # stable, persisted, never renamed
    name: str
    version: str
    host_api: int
    settings_model: type[BaseModel]           # validation + generated settings form (JSON schema)
    factory: Callable[[BaseModel], Any]       # settings -> provider instance
    provides: dict[str, Provide] = field(default_factory=dict)       # capability id -> how this plugin serves it
    requires: tuple[Requirement, ...] = ()                            # unmet => the plugin waits and offers nothing
    optional: tuple[Requirement, ...] = ()                            # resolved at call time
    defines: tuple[CapabilityDef, ...] = ()                           # capabilities this plugin introduces (ids start "<id>.")
    secret_fields: frozenset[str] = frozenset()   # write-only; never returned by any API
    ui: UiContribution = field(default_factory=UiContribution)
    # `schema` tabs: tab id -> the JSON the frontend renders (forms and tables over the plugin's own routes); None = no such tab
    ui_schema: Callable[[str], dict | None] | None = None
    routers: tuple[APIRouter, ...] = ()       # mounted under /api/v1/plugins/{id}/...
    alias_routers: tuple[APIRouter, ...] = () # deprecated aliases that keep their historical ABSOLUTE paths (mounted as-is)
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
            if tab.renderer == "schema" and self.ui_schema is None:
                raise PluginError(f"plugin {self.id!r}: schema tab {tab.id!r} needs a ui_schema provider")
        for cap, p in self.provides.items():
            if not CAP_ID_RE.match(cap):
                raise PluginError(f"plugin {self.id!r}: provides {cap!r}, which is not a capability id")
            if p.version < 1:
                raise PluginError(f"plugin {self.id!r}: provides {cap!r} with version {p.version}; versions start at 1")
        seen: set[str] = set()
        for d in self.defines:
            if d.id in CORE:
                raise PluginError(f"plugin {self.id!r}: cannot redefine the core capability {d.id!r}")
            if not d.id.startswith(f"{self.id}."):
                raise PluginError(f"plugin {self.id!r}: defined capability {d.id!r} must start with '{self.id}.'")
            if not CAP_ID_RE.match(d.id) or d.version < 1 or d.id in seen:
                raise PluginError(f"plugin {self.id!r}: bad or duplicate defined capability {d.id!r}")
            seen.add(d.id)
        for r in (*self.requires, *self.optional):
            if r.capability in self.provides:
                raise PluginError(f"plugin {self.id!r}: lists {r.capability!r} as required or optional, which it provides itself")
        for mod in self.migrations:
            for attr in ("version", "name", "up", "down"):
                if not hasattr(mod, attr):
                    raise PluginError(f"plugin {self.id!r}: migration {mod.__name__} lacks `{attr}` (down() is required)")

    @property
    def prefix(self) -> str:
        return self.table_prefix or f"{self.id}_"
