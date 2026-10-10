from __future__ import annotations

from pydantic import Field

from ..capabilities.notify_channel import ChannelSettings


class DiscordSettings(ChannelSettings):
    webhook_url: str | None = Field(default=None, title="Webhook URL", description="A Discord channel webhook URL (it contains a token: write-only)")
