from __future__ import annotations

from pydantic import Field

from ..capabilities.notify_channel import ChannelSettings


class EmailSettings(ChannelSettings):
    host: str | None = Field(default=None, title="SMTP host")
    port: int | None = Field(default=None, ge=1, le=65535, title="SMTP port", description="Usually 587 (STARTTLS) or 25")
    username: str | None = Field(default=None, description="Leave blank for an unauthenticated relay")
    password: str | None = Field(default=None, description="Write-only")
    from_addr: str | None = Field(default=None, title="From address")
    to_addrs: list[str] = Field(default_factory=list, title="To addresses", description="Recipients, comma-separated")
