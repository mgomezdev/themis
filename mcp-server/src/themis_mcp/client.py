"""Thin async client for the Themis REST API, authenticated with a (scoped) API key."""
from __future__ import annotations

import os
from typing import Any

import httpx


class ThemisError(Exception):
    """A Themis API call failed; the message is safe to show to the assistant (and the user)."""


def _detail(resp: httpx.Response) -> str:
    try:
        d = resp.json().get("detail")
    except Exception:
        d = None
    if isinstance(d, list):   # FastAPI validation errors
        d = "; ".join(str(e.get("msg", e)) for e in d)
    return str(d) if d else resp.reason_phrase or str(resp.status_code)


class ThemisClient:
    def __init__(self, base_url: str, api_key: str | None, *, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 20.0) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), transport=transport, timeout=timeout,
            headers={"X-Api-Key": api_key} if api_key else {},
        )

    @classmethod
    def from_env(cls) -> "ThemisClient":
        return cls(os.environ.get("THEMIS_URL", "http://localhost:8001"), os.environ.get("THEMIS_API_KEY"))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            resp = await self._http.request(method, f"/api/v1{path}", **kw)
        except httpx.HTTPError as e:
            raise ThemisError(f"Could not reach Themis at {self._http.base_url}: {e}") from e
        if resp.status_code == 401:
            raise ThemisError("Themis rejected the API key (missing, invalid, revoked or expired).")
        if resp.status_code == 403:
            raise ThemisError(f"Not allowed: {_detail(resp)}. Use an API key that has this scope.")
        if resp.status_code >= 400:
            raise ThemisError(f"Themis returned {resp.status_code}: {_detail(resp)}")
        return resp

    async def json(self, method: str, path: str, **kw: Any) -> Any:
        resp = await self._request(method, path, **kw)
        try:
            return resp.json()
        except ValueError as e:   # e.g. THEMIS_URL points at a web page or proxy instead of the Themis API
            raise ThemisError(f"{self._http.base_url} did not answer like Themis (expected JSON from {path}).") from e

    async def bytes(self, path: str) -> bytes:
        return (await self._request("GET", path)).content

    async def scopes(self) -> set[str] | None:
        """Scopes of the configured key, or None when the server is too old to say (then nothing is hidden)."""
        try:
            me = await self.json("GET", "/auth/me")
        except ThemisError:
            return None
        scopes = me.get("scopes") if isinstance(me, dict) else None
        return set(scopes) if isinstance(scopes, list) else None
