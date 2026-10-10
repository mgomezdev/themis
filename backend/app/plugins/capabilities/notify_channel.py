"""`notify.channel` — a fan-out capability (BIZ-252): every enabled channel plugin receives each human-facing notification, independently
(own timeout, own failure). A channel is configured entirely through its plugin settings (credentials as secret fields, an `events`
allow-list); core knows no channel. Webhooks for companion apps are a different thing (`webhook_service`, BIZ-172).

A provider implements `deliver(ChannelMessage) -> ChannelResult` (it decides whether it wants the event; `FilteredChannel` does the usual
allow-list) and `test_connection()` (sends a test message; raises with a readable reason on failure — the generic "Test connection" button
on the plugin page calls it). `ChannelMessage.message_id` is stable for a given notification, so a channel that retries can deduplicate."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from ...eventing.redaction import redact_error
from .definition import CapabilityDef, RoutedCapability

CAPABILITY = "notify.channel"

# The events a channel can subscribe to (offered as checkboxes on its settings page).
NOTIFY_EVENTS: tuple[str, ...] = (
    "job.complete", "job.failed", "job.blocked", "spool.low", "printer.alarm",
    "inventory.tracking_unavailable", "inventory.tracking_restored", "inventory.disconnected", "inventory.reconnected",
    "inventory.weight_conflict",
)
TEST_TITLE = "Themis test notification"
TEST_MESSAGE = "This is a test notification from Themis."


@dataclass(frozen=True)
class ChannelMessage:
    event: str                       # e.g. "job.complete"
    title: str
    message: str
    job_id: int | None = None
    message_id: str | None = None    # stable per notification (the event id when there is one)


@dataclass(frozen=True)
class ChannelResult:
    ok: bool
    skipped: bool = False            # the channel did not want this event / is not configured: not a failure
    error: str | None = None         # redacted, safe to log


@runtime_checkable
class NotificationChannel(Protocol):
    async def deliver(self, message: ChannelMessage) -> ChannelResult: ...

    async def test_connection(self) -> dict | None: ...


def events_field() -> object:
    """The `events` setting of a channel: which events it sends. Empty = none (an explicit opt-in); the schema lists the choices."""
    return Field(default_factory=list, title="Events", description="Which events send a notification on this channel.",
                 json_schema_extra={"items": {"type": "string", "enum": list(NOTIFY_EVENTS)}})


class ChannelSettings(BaseModel):
    events: list[str] = events_field()          # type: ignore[assignment]


class FilteredChannel:
    """Base for channels with an `events` allow-list: subclasses set `self.settings`, implement `configured()` and `send()`."""
    settings: ChannelSettings
    secrets: tuple[str, ...] = ()

    def configured(self) -> bool:
        raise NotImplementedError

    async def send(self, message: ChannelMessage) -> None:
        """Deliver or raise (the exception text is redacted by the caller)."""
        raise NotImplementedError

    async def deliver(self, message: ChannelMessage) -> ChannelResult:
        if message.event not in self.settings.events or not self.configured():
            return ChannelResult(True, skipped=True)
        try:
            await self.send(message)
        except Exception as exc:
            return ChannelResult(False, error=redact_error(exc, secrets=self.secrets))
        return ChannelResult(True)

    async def test_connection(self) -> dict | None:
        if not self.configured():
            raise RuntimeError("This channel is not fully configured yet")
        try:
            await self.send(ChannelMessage("test", TEST_TITLE, TEST_MESSAGE))
        except Exception as exc:
            raise RuntimeError(redact_error(exc, secrets=self.secrets)) from None
        return None


DEFINITION = CapabilityDef(
    id=CAPABILITY, version=1, label="Notification channel", mode="fan_out", protocol=NotificationChannel,
    description="Human-facing notifications (ntfy, Discord, email, …); every enabled channel receives each one, independently.")

NOTIFY_CHANNEL: RoutedCapability[NotificationChannel] = RoutedCapability(CAPABILITY)
