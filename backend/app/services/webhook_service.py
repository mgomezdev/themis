"""Fire-and-forget webhook delivery for job state events."""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0


def _signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def fire(url: str, secret: str | None, payload: dict) -> None:
    body = json.dumps(payload, default=str).encode()
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Webhook-Signature"] = _signature(secret, body)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, content=body, headers=headers)
        if not resp.is_success:
            logger.warning("Webhook POST %s → %s", url, resp.status_code)
    except Exception as exc:
        logger.warning("Webhook delivery failed for %s: %s", url, exc)


def schedule(url: str, secret: str | None, event: str, job_id: int, extra: dict | None = None) -> None:
    """Queue a fire-and-forget webhook delivery (safe to call from sync or async context)."""
    payload = {
        "event": event,
        "job_id": job_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(fire(url, secret, payload))
    except RuntimeError:
        logger.warning("No running loop — webhook for job %s skipped", job_id)
