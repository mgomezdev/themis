"""Manyfold library source. Disabled until explicitly configured and enabled."""

from ..capabilities.library_source import CAPABILITY
from ..manifest import HOST_API, PluginManifest, Provide, UiContribution, UiTab
from .provider import ManyfoldSource
from .settings import ManyfoldSettings

MANIFEST = PluginManifest(
    id="manyfold",
    name="Manyfold",
    version="1.0.0",
    host_api=HOST_API,
    default_enabled=False,
    settings_model=ManyfoldSettings,
    secret_fields=frozenset({"client_secret"}),
    factory=ManyfoldSource,
    provides={CAPABILITY: Provide()},
    ui=UiContribution(
        mode="page",
        nav_label="Manyfold",
        nav_placement="settings",
        tabs=(UiTab("connection", "Connection", "default"),),
    ),
    description="Read model catalogs and fetch files from an authenticated Manyfold library.",
    docs_url="https://manyfold.app",
)
