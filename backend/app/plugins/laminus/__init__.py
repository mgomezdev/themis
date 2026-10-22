"""The Laminus plugin (BIZ-260): owns the connection config of the slicing service (URL) and its connection test. Bundled.
Slice settings stay Themis-owned (docs/architecture/laminus-contract.md); this plugin is connection/engine config only."""
from __future__ import annotations

from ...services.providers.laminus.connection import PLUGIN_VERSION, LaminusConnection, LaminusSettings
from ..manifest import HOST_API, PluginManifest, Provide, UiContribution, UiTab

MANIFEST = PluginManifest(
    id="laminus",
    name="Laminus",
    version=PLUGIN_VERSION,
    host_api=HOST_API,
    settings_model=LaminusSettings,
    factory=LaminusConnection,
    provides={"gcode.produce": Provide(version=1)},
    default_enabled=True,
    ui=UiContribution(mode="page", nav_label="Laminus", nav_placement="settings", connection_status=True,
                      tabs=(UiTab("connection", "Connection", "default"),)),
    description="The Laminus slicing service Themis slices and builds projects with: its URL and connection test.",
)
