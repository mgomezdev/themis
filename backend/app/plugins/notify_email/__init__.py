"""The email notification channel plugin (BIZ-252): a provider of the fan-out `notify.channel` capability."""
from __future__ import annotations

from ..capabilities.notify_channel import CAPABILITY
from ..manifest import HOST_API, PluginManifest, Provide, UiContribution, UiTab
from .channel import EmailChannel
from .settings import EmailSettings

MANIFEST = PluginManifest(
    id="notify_email",
    name="Email",
    version="1.0.0",
    host_api=HOST_API,
    settings_model=EmailSettings,
    secret_fields=frozenset({"password"}),
    factory=EmailChannel,
    provides={CAPABILITY: Provide(version=1)},
    default_enabled=True,
    ui=UiContribution(mode="page", nav_label="Email", nav_placement="settings", nav_icon="bell",
                      tabs=(UiTab("settings", "Settings", "default"),)),
    description="Notifications sent by email through an SMTP server.",
)
