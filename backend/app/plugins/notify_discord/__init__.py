"""The Discord notification channel plugin (BIZ-252): a provider of the fan-out `notify.channel` capability."""
from __future__ import annotations

from ..capabilities.notify_channel import CAPABILITY
from ..manifest import HOST_API, PluginManifest, Provide, UiContribution, UiTab
from .channel import DiscordChannel
from .settings import DiscordSettings

MANIFEST = PluginManifest(
    id="notify_discord",
    name="Discord",
    version="1.0.0",
    host_api=HOST_API,
    settings_model=DiscordSettings,
    secret_fields=frozenset({"webhook_url"}),
    factory=DiscordChannel,
    provides={CAPABILITY: Provide(version=1)},
    default_enabled=True,
    ui=UiContribution(mode="page", nav_label="Discord", nav_placement="settings", nav_icon="bell",
                      tabs=(UiTab("settings", "Settings", "default"),)),
    description="Notifications posted to a Discord channel through its webhook.",
)
