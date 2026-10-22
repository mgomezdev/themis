"""Seed webhook destinations (BIZ-172) in tests."""
from __future__ import annotations

from app.models import WebhookDestination


def destination(url: str | None = "http://hook.test", secret: str | None = None, events=(), name: str = "default",
                enabled: bool = True) -> WebhookDestination:
    return WebhookDestination(name=name, url=url, secret=secret, events=list(events), enabled=enabled,
                              created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00")


class Wire:
    """Records what a webhook receiver would see. `responses` is consumed one per request (an Exception is raised, an int is the
    status); once empty, `default` is used."""
    def __init__(self) -> None:
        self.requests: list = []
        self.responses: list = []
        self.default: int | Exception = 200
        self.by_host: dict[str, int | Exception] = {}

    def payloads(self, event: str | None = None) -> list[dict]:
        import json
        out = [json.loads(r.content) for r in self.requests]
        return [p for p in out if event is None or p.get("event") == event]


def install_wire(monkeypatch) -> Wire:
    """Route webhook_service's HTTP to a recording MockTransport and make retries instant."""
    import httpx
    from app.services import webhook_service

    wire = Wire()

    def handler(request: httpx.Request) -> httpx.Response:
        wire.requests.append(request)
        outcome = wire.by_host.get(request.url.host) if request.url.host in wire.by_host else (
            wire.responses.pop(0) if wire.responses else wire.default)
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(webhook_service.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(webhook_service, "RETRY_DELAYS_S", (0.0, 0.0))
    return wire


def verify_signature(secret: str, request) -> bool:
    """What a receiver does: recompute HMAC-SHA256 over the raw body and compare to X-Webhook-Signature."""
    import hashlib
    import hmac
    expected = "sha256=" + hmac.new(secret.encode(), request.content, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, request.headers.get("x-webhook-signature", ""))
