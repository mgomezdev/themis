from __future__ import annotations

from pydantic import Field

from ..capabilities.notify_channel import ChannelSettings


class NtfySettings(ChannelSettings):
    server_url: str | None = Field(default=None, title="Server URL", description="A self-hosted or public ntfy server, e.g. https://ntfy.sh")
    topic: str | None = Field(default=None, description="The topic to publish to (anyone who knows it can read it on a public server)")
    priority: int | None = Field(default=None, ge=1, le=5, description="ntfy priority 1 (min) to 5 (max); blank = the server default")
