"""The ntfy notification channel plugin (BIZ-252): a provider of the fan-out `notify.channel` capability. Bundled; configured on its own
settings page (server, topic, priority and the events it sends)."""
from __future__ import annotations

from ..capabilities.notify_channel import CAPABILITY
from ..manifest import HOST_API, PluginManifest, Provide, UiContribution, UiTab
from .channel import NtfyChannel
from .settings import NtfySettings

MANIFEST = PluginManifest(
    id="notify_ntfy",
    name="ntfy",
    version="1.0.0",
    host_api=HOST_API,
    settings_model=NtfySettings,
    factory=NtfyChannel,
    provides={CAPABILITY: Provide(version=1)},
    default_enabled=True,
    ui=UiContribution(mode="page", nav_label="ntfy", nav_placement="settings", nav_icon="bell",
                      tabs=(UiTab("settings", "Settings", "default"),)),
    description="Push notifications via a self-hosted or public ntfy server.",
    docs_url="https://docs.ntfy.sh",
)
