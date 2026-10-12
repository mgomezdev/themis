"""Laminus connection config (BIZ-260): the plugin-owned settings (service URL) and the connection test with distinct
unconfigured / unavailable / healthy states. The plugin package `app/plugins/laminus` exposes these through the plugin host;
core reads the URL via `slicing.configured_server_url()` and gets the adapter from `LaminusConnection.slicing_provider()`
(the plugin instance owns the connection-level options below; slicing settings stay Themis-owned).

The URL arrives already resolved by the host (BIZ-289): saved setting > deprecated `LAMINUS_SIDECAR_URL` (read by core) >
`app/plugins/laminus/defaults.toml`. This module never reads the environment."""
from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field, field_validator

from ..slicing import SlicingProviderError
from .sidecar_client import DEFAULT_POLL_TIMEOUT_S, DEFAULT_REQUEST_TIMEOUT_S

DEFAULT_HEALTH_TIMEOUT_S = 5.0

UNCONFIGURED, UNAVAILABLE, HEALTHY = "unconfigured", "unavailable", "healthy"


class LaminusSettings(BaseModel):
    url: str | None = Field(
        default=None, title="Service URL",
        description="Base URL of the Laminus service, e.g. http://orca:5000. Blank uses the shipped default.")

    # Connection-level tuning (BIZ-288). Shipped values live in the plugin's defaults.toml; blank/null = use them (the model
    # default is the same number, so a bare model behaves identically).
    health_timeout_s: float | None = Field(
        default=DEFAULT_HEALTH_TIMEOUT_S, gt=0, le=600, title="Health check timeout (s)",
        description="How long the connection test waits for the service to answer.")
    request_timeout_s: float | None = Field(
        default=DEFAULT_REQUEST_TIMEOUT_S, gt=0, le=7200, title="Request timeout (s)",
        description="Per-request client timeout for slice, arrange and pack calls. Keep it above the service's own slice timeout (600 s).")
    poll_timeout_s: float | None = Field(
        default=DEFAULT_POLL_TIMEOUT_S, gt=0, le=7200, title="Slice wait ceiling (s)",
        description="How long Themis waits for a slice job to finish before giving up on it.")

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
        self.health_timeout_s = settings.health_timeout_s or DEFAULT_HEALTH_TIMEOUT_S
        self.request_timeout_s = settings.request_timeout_s or DEFAULT_REQUEST_TIMEOUT_S
        self.poll_timeout_s = settings.poll_timeout_s or DEFAULT_POLL_TIMEOUT_S

    def slicing_provider(self, url: str | None = None):
        """The adapter for `url` (default: the configured one) carrying this plugin's options; None while unconfigured."""
        url = url or self.url
        if not url:
            return None
        from .adapter import LaminusSlicingProvider
        return LaminusSlicingProvider(url, request_timeout_s=self.request_timeout_s, poll_timeout_s=self.poll_timeout_s)

    async def test_connection(self) -> dict:
        if not self.url:
            raise NotConfigured("Not configured: set the Laminus service URL.")
        try:
            info = await asyncio.to_thread(self.slicing_provider().health, self.health_timeout_s)
        except SlicingProviderError as e:                  # includes "not ready" (still starting / building its catalog)
            raise Unavailable(f"Unavailable: {e}") from e
        return {"state": HEALTHY, "version": info.get("version") if isinstance(info, dict) else None}
