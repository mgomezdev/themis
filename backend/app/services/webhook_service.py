"""Outbound webhooks (BIZ-172; docs/companion-apps.md).

Destinations (`webhook_destinations`) are independent: each has its own URL, secret, event filter and enabled flag. `dispatch` fans an
event out to every enabled destination whose filter matches; each delivery is a background task that retries a bounded number of times.

Wire format (v1): a JSON POST,
  body    {"event", "event_id", "schema_version": 1, "timestamp", ["job_id"], ...event fields}
  headers X-Webhook-Id (= event_id, identical on every attempt: receivers deduplicate on it), X-Webhook-Event, X-Webhook-Timestamp,
          X-Webhook-Signature = "sha256=" + HMAC-SHA256(secret, raw body) when the destination has a secret.
Retries: up to MAX_ATTEMPTS (3) attempts at 0 s, +2 s, +10 s, only after a network error/timeout, HTTP 429 or 5xx. Any other response
(including other 4xx) is final. Delivery is at-least-once within the process: a restart drops deliveries still waiting to retry."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..eventing.redaction import redact_error
from ..models import WebhookDestination

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0
SCHEMA_VERSION = 1
MAX_ATTEMPTS = 3
RETRY_DELAYS_S = (2.0, 10.0)            # waited before attempt 2 and attempt 3
DEFAULT_DESTINATION = "default"          # the destination the legacy single-webhook settings page edits

_tasks: set[asyncio.Task] = set()


def _signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


now_iso = _now


def _safe_url(url: str) -> str:
    """The URL without credentials or query string (a token often rides there), for logs."""
    p = urlsplit(url)
    host = p.hostname or ""
    return urlunsplit((p.scheme, f"{host}:{p.port}" if p.port else host, p.path, "", ""))


@dataclass(frozen=True)
class Outcome:
    delivered: bool
    status: int | None = None
    error: str | None = None

    @property
    def retryable(self) -> bool:
        """A network failure, 429 or 5xx may succeed later; everything else is the receiver's final answer."""
        return not self.delivered and (self.status is None or self.status == 429 or self.status >= 500)


def build_payload(event: str, job_id: int | None, extra: dict | None, event_id: str | None = None) -> dict:
    return {
        "event": event,
        "event_id": event_id or uuid.uuid4().hex,
        "schema_version": SCHEMA_VERSION,
        # Non-job events (e.g. spool.low, project.created) carry no job_id.
        **({"job_id": job_id} if job_id is not None else {}),
        "timestamp": _now(),
        **(extra or {}),
    }


async def attempt(url: str, secret: str | None, payload: dict) -> Outcome:
    """One signed POST."""
    body = json.dumps(payload, default=str).encode()
    headers = {"Content-Type": "application/json"}
    if payload.get("event_id"):
        headers["X-Webhook-Id"] = str(payload["event_id"])
    if payload.get("event"):
        headers["X-Webhook-Event"] = str(payload["event"])
    if payload.get("timestamp"):
        headers["X-Webhook-Timestamp"] = str(payload["timestamp"])
    if secret:
        headers["X-Webhook-Signature"] = _signature(secret, body)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, content=body, headers=headers)
    except Exception as exc:
        logger.warning("Webhook delivery failed for %s: %s", _safe_url(url), redact_error(exc, secrets=(secret or "",)))
        return Outcome(False, None, redact_error(exc, secrets=(secret or "",)))
    if not resp.is_success:
        logger.warning("Webhook POST %s → %s", _safe_url(url), resp.status_code)
        return Outcome(False, resp.status_code, f"HTTP {resp.status_code}")
    return Outcome(True, resp.status_code)


async def fire(url: str, secret: str | None, payload: dict) -> None:
    """A single attempt; failures are logged, never raised."""
    await attempt(url, secret, payload)


async def deliver(url: str, secret: str | None, payload: dict, *, destination_id: int | None = None,
                  factory: async_sessionmaker[AsyncSession] | None = None) -> Outcome:
    """Send with bounded retries (same body and event id every time), then record the outcome on the destination."""
    outcome = Outcome(False, None, "not attempted")
    for n in range(MAX_ATTEMPTS):
        if n:
            await asyncio.sleep(RETRY_DELAYS_S[n - 1])
        outcome = await attempt(url, secret, payload)
        if not outcome.retryable:
            break
    if outcome.retryable:
        logger.warning("Webhook %s to %s gave up after %d attempts", payload.get("event"), _safe_url(url), MAX_ATTEMPTS)
    if destination_id is not None and factory is not None:
        await _record(factory, destination_id, outcome)
    return outcome


async def _record(factory: async_sessionmaker[AsyncSession], destination_id: int, outcome: Outcome) -> None:
    try:
        async with factory() as session:
            row = await session.get(WebhookDestination, destination_id)
            if row is None:
                return
            row.last_attempt_at = _now()
            row.last_status, row.last_error = outcome.status, outcome.error
            if outcome.delivered:
                row.last_success_at = row.last_attempt_at
            await session.commit()
    except Exception:
        logger.exception("Could not record the webhook outcome for destination %s", destination_id)


def schedule(url: str, secret: str | None, event: str, job_id: int | None, extra: dict | None = None, *,
             destination_id: int | None = None, event_id: str | None = None,
             factory: async_sessionmaker[AsyncSession] | None = None) -> None:
    """Queue a fire-and-forget delivery (safe to call from sync or async context)."""
    payload = build_payload(event, job_id, extra, event_id)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("No running loop — webhook for job %s skipped", job_id)
        return
    task = loop.create_task(deliver(url, secret, payload, destination_id=destination_id, factory=factory))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def drain() -> None:
    """Wait for every queued delivery (tests, shutdown)."""
    while True:
        _tasks.difference_update({t for t in _tasks if t.done()})        # a finished task whose loop is gone never runs its callback
        if not _tasks:
            return
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def cancel_all() -> None:
    """Abandon deliveries that are still waiting to retry (shutdown, tests)."""
    pending = [t for t in _tasks if not t.done()]
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    _tasks.clear()


async def destinations_for(session: AsyncSession, event: str) -> list[WebhookDestination]:
    rows = (await session.execute(select(WebhookDestination).where(WebhookDestination.enabled.is_(True))
                                  .order_by(WebhookDestination.id))).scalars().all()
    return [d for d in rows if d.url and (not d.events or event in d.events)]


async def dispatch(session: AsyncSession, event: str, job_id: int | None = None, extra: dict | None = None, *,
                   event_id: str | None = None) -> int:
    """Send `event` to every enabled destination subscribed to it (one shared `event_id`). Returns how many were scheduled."""
    destinations = await destinations_for(session, event)
    if not destinations:
        return 0
    event_id = event_id or uuid.uuid4().hex
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    for d in destinations:
        schedule(d.url, d.secret, event, job_id, extra, destination_id=d.id, event_id=event_id, factory=factory)
    return len(destinations)


async def default_destination(session: AsyncSession, *, create: bool = False) -> WebhookDestination | None:
    row = (await session.execute(select(WebhookDestination).where(WebhookDestination.name == DEFAULT_DESTINATION))).scalar_one_or_none()
    if row is None and create:
        now = _now()
        row = WebhookDestination(name=DEFAULT_DESTINATION, url=None, secret=None, events=[], enabled=True, created_at=now, updated_at=now)
        session.add(row)
        await session.flush()
    return row
