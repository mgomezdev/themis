from pydantic import BaseModel, Field


class ManyfoldSettings(BaseModel):
    url: str | None = Field(default=None, title="Manyfold base URL")
    client_id: str | None = Field(default=None, title="OAuth client ID")
    client_secret: str | None = Field(default=None, title="OAuth client secret")
    sync_interval_minutes: int = Field(
        default=60,
        title="Catalog sync interval (minutes)",
        ge=0,
        le=10080,
        description="Catalog sync interval; 0 for manual sync only",
    )
    request_timeout_s: float = Field(default=15, gt=0, le=120)
    max_download_bytes: int = Field(
        default=512 * 1024 * 1024, gt=0, le=4 * 1024 * 1024 * 1024
    )
