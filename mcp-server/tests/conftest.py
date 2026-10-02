from __future__ import annotations

import json

import httpx
import pytest

from themis_mcp.client import ThemisClient


class FakeThemis:
    """An httpx MockTransport standing in for the Themis API; records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], object] = {}

    def route(self, method: str, path: str, response) -> None:
        """response: JSON-able, bytes, httpx.Response, or a callable(request) -> any of those."""
        self.routes[(method, f"/api/v1{path}")] = response

    def calls(self, method: str | None = None) -> list[tuple[str, str]]:
        return [(r.method, r.url.path.removeprefix("/api/v1")) for r in self.requests if method in (None, r.method)]

    def posts(self) -> list[str]:
        return [p for m, p in self.calls() if m != "GET"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        resp = self.routes.get((request.method, request.url.path))
        if resp is None:
            return httpx.Response(404, json={"detail": f"no fake route {request.method} {request.url.path}"})
        if callable(resp):
            resp = resp(request)
        if isinstance(resp, httpx.Response):
            return resp
        if isinstance(resp, bytes):
            return httpx.Response(200, content=resp, headers={"content-type": "image/jpeg"})
        return httpx.Response(200, content=json.dumps(resp), headers={"content-type": "application/json"})


@pytest.fixture
def fake() -> FakeThemis:
    return FakeThemis()


@pytest.fixture
def api(fake: FakeThemis) -> ThemisClient:
    return ThemisClient("http://themis.test", "thm_secret", transport=httpx.MockTransport(fake))
