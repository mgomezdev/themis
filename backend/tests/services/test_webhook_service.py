"""Webhook delivery: HMAC-signed, fire-and-forget POSTs for job events. Requests go through real httpx with a
MockTransport, so what is asserted is what a receiver would actually get on the wire."""
import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from app.models import WebhookConfig
from app.services import webhook_service
from app.services.printer_manager import PrinterManager
from app.services.queue_engine import QueueEngine

URL = "https://hooks.example.test/themis"


@pytest.fixture
def wire(monkeypatch):
    """Route webhook_service's httpx traffic to a recording MockTransport. `wire.status` / `wire.error` steer the reply."""
    class Wire:
        requests: list[httpx.Request] = []
        status = 200
        error: Exception | None = None

    w = Wire()
    w.requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        w.requests.append(request)
        if w.error is not None:
            raise w.error
        return httpx.Response(w.status)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(webhook_service.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    return w


async def _drain_background_tasks() -> None:
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*pending)


async def test_fire_signs_the_exact_body_bytes_with_hmac_sha256(wire):
    await webhook_service.fire(URL, "s3cret", {"event": "job.complete", "job_id": 7})

    (request,) = wire.requests
    expected = "sha256=" + hmac.new(b"s3cret", request.content, hashlib.sha256).hexdigest()
    assert request.headers["x-webhook-signature"] == expected
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {"event": "job.complete", "job_id": 7}
    assert str(request.url) == URL and request.method == "POST"


@pytest.mark.parametrize("secret", [None, ""])
async def test_fire_without_a_secret_sends_no_signature(wire, secret):
    await webhook_service.fire(URL, secret, {"event": "job.failed", "job_id": 1})

    (request,) = wire.requests
    assert "x-webhook-signature" not in request.headers


async def test_fire_serializes_non_json_values_instead_of_raising(wire):
    stamp = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    await webhook_service.fire(URL, None, {"event": "job.complete", "at": stamp})

    assert json.loads(wire.requests[0].content)["at"] == str(stamp)


async def test_fire_swallows_a_non_2xx_response_and_logs_it(wire, caplog):
    wire.status = 500
    with caplog.at_level(logging.WARNING, logger=webhook_service.logger.name):
        assert await webhook_service.fire(URL, "s", {"event": "job.complete"}) is None

    assert len(wire.requests) == 1
    assert any("500" in r.getMessage() and URL in r.getMessage() for r in caplog.records)


async def test_fire_swallows_a_transport_error_and_logs_it(wire, caplog):
    wire.error = httpx.ConnectError("refused")
    with caplog.at_level(logging.WARNING, logger=webhook_service.logger.name):
        assert await webhook_service.fire(URL, "s", {"event": "job.complete"}) is None

    assert any("Webhook delivery failed" in r.getMessage() and "refused" in r.getMessage() for r in caplog.records)


async def test_schedule_returns_before_delivery_and_delivers_event_job_and_extras(wire):
    webhook_service.schedule(URL, "s3cret", "job.blocked", 42, extra={"printer_id": 3})

    assert wire.requests == [], "schedule() must not wait for the network (fire-and-forget)"
    await _drain_background_tasks()

    (request,) = wire.requests
    body = json.loads(request.content)
    assert body["event"] == "job.blocked"
    assert body["job_id"] == 42
    assert body["printer_id"] == 3
    assert datetime.fromisoformat(body["timestamp"]).tzinfo is not None
    assert "x-webhook-signature" in request.headers


def test_schedule_outside_an_event_loop_logs_and_does_not_raise(wire, caplog):
    with caplog.at_level(logging.WARNING, logger=webhook_service.logger.name):
        webhook_service.schedule(URL, "s", "job.complete", 9)

    assert wire.requests == []
    assert any("No running loop" in r.getMessage() and "9" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Engine wiring: which events reach schedule(), and with what
# ---------------------------------------------------------------------------

@pytest.fixture
def engine(session_factory):
    eng = QueueEngine(session_factory, PrinterManager(), MagicMock())
    yield eng
    eng._executor.shutdown(wait=False)


async def _configure(session_factory, **fields):
    async with session_factory() as s:
        s.add(WebhookConfig(id=1, **fields))
        await s.commit()


async def test_engine_schedules_a_webhook_for_a_subscribed_event(engine, session_factory, monkeypatch):
    schedule = MagicMock()
    monkeypatch.setattr("app.services.queue_engine.webhook_service.schedule", schedule)
    await _configure(session_factory, url=URL, secret="s", events=["job.complete"])

    await engine._fire_webhooks(5, "job.complete")

    schedule.assert_called_once_with(URL, "s", "job.complete", 5)


async def test_engine_treats_an_empty_event_list_as_all_events(engine, session_factory, monkeypatch):
    schedule = MagicMock()
    monkeypatch.setattr("app.services.queue_engine.webhook_service.schedule", schedule)
    await _configure(session_factory, url=URL, secret=None, events=[])

    await engine._fire_webhooks(5, "job.failed")

    schedule.assert_called_once_with(URL, None, "job.failed", 5)


@pytest.mark.parametrize("config", [
    pytest.param(None, id="no-config-row"),
    pytest.param({"url": None, "events": []}, id="no-url"),
    pytest.param({"url": "", "events": []}, id="blank-url"),
    pytest.param({"url": URL, "events": ["job.complete"]}, id="event-not-subscribed"),
])
async def test_engine_sends_nothing_when_unconfigured_or_unsubscribed(engine, session_factory, monkeypatch, config):
    schedule = MagicMock()
    monkeypatch.setattr("app.services.queue_engine.webhook_service.schedule", schedule)
    if config is not None:
        await _configure(session_factory, **config)

    await engine._fire_webhooks(5, "job.failed")

    schedule.assert_not_called()
