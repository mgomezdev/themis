"""The Local inventory plugin: a `filament_inventory` provider that keeps materials, spools and weights in Themis' own
database (plugin-owned `local_inv_*` tables). An ordinary plugin: nothing in core knows it (spec §3.6, D3/D7/D8)."""
from __future__ import annotations

from ..capabilities.filament_inventory import CAPABILITY
from ..manifest import HOST_API, PluginManifest, Provide, UiContribution, UiTab
from .migrations import v001_tables
from .provider import LocalInventoryProvider
from .routes import router
from .settings import LocalInventorySettings

MANIFEST = PluginManifest(
    id="local_inventory",
    name="Local inventory",
    version="1.0.0",
    host_api=HOST_API,
    settings_model=LocalInventorySettings,
    factory=LocalInventoryProvider,
    provides={CAPABILITY: Provide(version=1, features=LocalInventoryProvider.capabilities, routers=(router,))},
    migrations=(v001_tables,),
    table_prefix="local_inv_",
    ui=UiContribution(mode="page", nav_label="Local inventory", nav_placement="settings",
                      tabs=(UiTab("settings", "Settings", "default"),)),
    description="A built-in filament library: materials, spools, weights and storage locations kept in Themis itself.",
)
