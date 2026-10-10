"""Send a human-facing notification to every enabled `notify.channel` plugin (BIZ-252). Core knows no channel: it builds a neutral
`ChannelMessage` and fans it out through the plugin host (each channel contained on its own: a slow or failing one never delays or
fails another, and nothing here raises). A `message_id` seen before is dropped, so a redelivered event (`job.complete` is
at-least-once) does not notify twice."""
from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import datetime, timezone

from ..eventing.redaction import redact_error
from ..plugins.capabilities.notify_channel import NOTIFY_CHANNEL, ChannelMessage
from ..plugins.host import plugin_host

logger = logging.getLogger("app")

CHANNEL_TIMEOUT_S = 15.0
_SEEN_LIMIT = 1000
_seen: OrderedDict[str, None] = OrderedDict()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _first_time(message_id: str | None) -> bool:
    if message_id is None:
        return True
    if message_id in _seen:
        return False
    _seen[message_id] = None
    while len(_seen) > _SEEN_LIMIT:
        _seen.popitem(last=False)
    return True


async def notify(event: str, job_id: int | None, title: str, message: str, *, message_id: str | None = None) -> dict[str, str]:
    """Fan the notification out. Returns `{plugin_id: "ok" | "skipped" | <redacted error>}` (tests, diagnostics); never raises."""
    outcome: dict[str, str] = {}
    try:
        if not _first_time(message_id):
            return outcome
        msg = ChannelMessage(event, title, message, job_id, message_id)
        results = await plugin_host.fan_out(NOTIFY_CHANNEL, lambda p: p.deliver(msg), timeout=CHANNEL_TIMEOUT_S)
        for plugin_id, res in results.items():
            if not res.ok:
                outcome[plugin_id] = redact_error(res.error or "failed")
            elif res.value is not None and not res.value.ok:
                outcome[plugin_id] = res.value.error or "failed"
            else:
                outcome[plugin_id] = "skipped" if getattr(res.value, "skipped", False) else "ok"
            if outcome[plugin_id] not in ("ok", "skipped"):
                logger.warning("Notification channel %s failed for %s: %s", plugin_id, event, outcome[plugin_id])
                await plugin_host.record_state(plugin_id, last_error=outcome[plugin_id], last_error_at=_now())   # shows on its settings page
            elif outcome[plugin_id] == "ok":
                await plugin_host.record_state(plugin_id, last_error=None, last_ok_at=_now())
    except Exception:
        logger.exception("Could not send the %s notification", event)
    return outcome
