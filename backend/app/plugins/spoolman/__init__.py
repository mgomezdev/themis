"""The Spoolman plugin: a `filament_inventory` provider (bundled; spec §3.9 D9 — it owns every Spoolman call)."""
from __future__ import annotations

from ..capabilities.filament_inventory import KIND
from ..manifest import HOST_API, PluginManifest, UiContribution, UiTab
from .provider import SpoolmanProvider
from .routes import router as alias_router, settings_router as alias_settings_router
from .settings import SpoolmanSettings

MANIFEST = PluginManifest(
    id="spoolman",
    name="Spoolman",
    kind=KIND,
    version="1.0.0",
    host_api=HOST_API,
    settings_model=SpoolmanSettings,
    secret_fields=frozenset({"api_key"}),
    factory=SpoolmanProvider,
    capabilities=SpoolmanProvider.capabilities,
    alias_routers=(alias_router, alias_settings_router),
    ui=UiContribution(mode="page", nav_label="Spoolman", nav_placement="settings",
                      tabs=(UiTab("connection", "Connection", "default"),
                            UiTab("mappings", "Filament mappings", "component"))),
    description="Filament inventory from a Spoolman instance: spools, materials, weights and Orca preset links.",
    docs_url="https://github.com/Donkie/Spoolman",
)
