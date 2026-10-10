"""Discord notifications through a channel webhook (BIZ-252)."""
from __future__ import annotations

import httpx

from ..capabilities.notify_channel import ChannelMessage, FilteredChannel
from .settings import DiscordSettings

_TIMEOUT = 5.0


class DiscordChannel(FilteredChannel):
    def __init__(self, settings: DiscordSettings) -> None:
        self.settings = settings
        self.secrets = (settings.webhook_url or "",)

    def configured(self) -> bool:
        return bool(self.settings.webhook_url)

    async def send(self, message: ChannelMessage) -> None:
        # A delivered notification posts its message text; the test posts "title\nmessage" (as the old settings page did).
        content = f"{message.title}\n{message.message}" if message.event == "test" else message.message
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(self.settings.webhook_url or "", json={"content": content})
        if not resp.is_success:
            raise RuntimeError(f"Discord webhook responded {resp.status_code}")
