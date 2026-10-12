"""Laminus connection config (BIZ-260): the plugin-owned settings (service URL), the compatible env default
(`LAMINUS_SIDECAR_URL`) and the connection test with distinct unconfigured / unavailable / healthy states. The plugin
package `app/plugins/laminus` exposes these through the plugin host; core reads the URL via `slicing.configured_server_url()`."""
from __future__ import annotations

import asyncio
import os

from pydantic import BaseModel, Field, field_validator

from ..slicing import SlicingProviderError

ENV_URL = "LAMINUS_SIDECAR_URL"          # pre-plugin deployments set this; read as the default when no URL is saved
HEALTH_TIMEOUT_S = 5.0

UNCONFIGURED, UNAVAILABLE, HEALTHY = "unconfigured", "unavailable", "healthy"


class LaminusSettings(BaseModel):
    url: str | None = Field(
        default=None, title="Service URL",
        description=f"Base URL of the Laminus service, e.g. http://orca:5000. Blank uses the {ENV_URL} environment variable, if set.")

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


def env_url() -> str | None:
    return (os.environ.get(ENV_URL) or "").strip().rstrip("/") or None


def effective_url(saved: str | None) -> str | None:
    """The saved URL, else the env default. None = unconfigured."""
    return (saved or "").strip().rstrip("/") or env_url()


class LaminusConnection:
    """The plugin instance: holds validated settings and answers `test_connection` (also used for unsaved drafts)."""

    def __init__(self, settings: LaminusSettings) -> None:
        self.url = effective_url(settings.url)

    async def test_connection(self) -> dict:
        if not self.url:
            raise NotConfigured(f"Not configured: set the Laminus service URL (or {ENV_URL}).")
        from .adapter import LaminusSlicingProvider
        try:
            info = await asyncio.to_thread(LaminusSlicingProvider(self.url).health, HEALTH_TIMEOUT_S)
        except SlicingProviderError as e:                  # includes "not ready" (still starting / building its catalog)
            raise Unavailable(f"Unavailable: {e}") from e
        return {"state": HEALTHY, "version": info.get("version") if isinstance(info, dict) else None}
