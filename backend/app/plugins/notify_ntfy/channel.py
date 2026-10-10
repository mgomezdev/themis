"""ntfy push notifications (BIZ-252): the sender that used to live in `services/notification_service.py`."""
from __future__ import annotations

import httpx

from ..capabilities.notify_channel import ChannelMessage, FilteredChannel
from .settings import NtfySettings

_TIMEOUT = 5.0


class NtfyChannel(FilteredChannel):
    def __init__(self, settings: NtfySettings) -> None:
        self.settings = settings

    def configured(self) -> bool:
        return bool(self.settings.server_url and self.settings.topic)

    async def send(self, message: ChannelMessage) -> None:
        body: dict = {"topic": self.settings.topic, "title": message.title, "message": message.message}
        if self.settings.priority is not None:
            body["priority"] = self.settings.priority
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post((self.settings.server_url or "").rstrip("/"), json=body)
        if not resp.is_success:
            raise RuntimeError(f"ntfy server responded {resp.status_code}")
