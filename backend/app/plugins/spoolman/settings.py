from __future__ import annotations

from pydantic import BaseModel, Field


class SpoolmanSettings(BaseModel):
    url: str | None = Field(default=None, description="Base URL of the Spoolman instance, e.g. http://spoolman:7912")
    api_key: str | None = Field(default=None, description="Only needed when Spoolman sits behind an API-key proxy")
    sync_interval_minutes: int = Field(default=15, ge=1, description="How often Themis refreshes spools and filaments")
    max_disconnect_minutes: int | None = Field(
        default=None, ge=1, description="Alert after Spoolman has been unreachable this long (blank = never)")
