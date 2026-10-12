"""Laminus connection config (BIZ-260): the plugin-owned settings (service URL) and the connection test with distinct
unconfigured / unavailable / healthy states. The plugin package `app/plugins/laminus` exposes these through the plugin host;
core reads the URL via `slicing.configured_server_url()`.

The URL arrives already resolved by the host (BIZ-289): saved setting > deprecated `LAMINUS_SIDECAR_URL` (read by core) >
`app/plugins/laminus/defaults.toml`. This module never reads the environment."""
from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field, field_validator

from ..slicing import SlicingProviderError

HEALTH_TIMEOUT_S = 5.0

UNCONFIGURED, UNAVAILABLE, HEALTHY = "unconfigured", "unavailable", "healthy"


class LaminusSettings(BaseModel):
    url: str | None = Field(
        default=None, title="Service URL",
        description="Base URL of the Laminus service, e.g. http://orca:5000. Blank uses the shipped default.")

    @field_validator("url")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        v = (v or "").strip().rstrip("/")
        if not v:
            return None
        if not v.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return v


class ConnectionStateError(Exception):
    state = UNAVAILABLE


class NotConfigured(ConnectionStateError):
    state = UNCONFIGURED


class Unavailable(ConnectionStateError):
    state = UNAVAILABLE


def clean_url(value: str | None) -> str | None:
    """Normalise a resolved URL (strip, no trailing slash). None = unconfigured."""
    return (value or "").strip().rstrip("/") or None


class LaminusConnection:
    """The plugin instance: holds validated settings and answers `test_connection` (also used for unsaved drafts)."""

    def __init__(self, settings: LaminusSettings) -> None:
        self.url = clean_url(settings.url)

    async def test_connection(self) -> dict:
        if not self.url:
            raise NotConfigured("Not configured: set the Laminus service URL.")
        from .adapter import LaminusSlicingProvider
        try:
            info = await asyncio.to_thread(LaminusSlicingProvider(self.url).health, HEALTH_TIMEOUT_S)
        except SlicingProviderError as e:                  # includes "not ready" (still starting / building its catalog)
            raise Unavailable(f"Unavailable: {e}") from e
        return {"state": HEALTHY, "version": info.get("version") if isinstance(info, dict) else None}
