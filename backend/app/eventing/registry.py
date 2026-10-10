"""The event catalog: core events plus every registered plugin's `defines_events` (BIZ-249). Publication is validated against it."""
from __future__ import annotations

from .definitions import EventDef
from .envelope import EventEnvelope, EventError

# Core event classes. `job.complete` is durable: its consumers (inventory deduction, maintenance accrual) must not lose it.
# The rest are notices: losing one on a crash costs a notification, never data. Names match the existing webhook/notification
# event names so migrating a consumer changes how it is fed, not what it is called.
CORE_EVENTS: dict[str, EventDef] = {d.name: d for d in (
    EventDef("job.complete", durability="durable", description="A job finished printing successfully (published once per job)."),
    EventDef("job.failed", description="A job failed after slicing (upload or start error)."),
    EventDef("job.blocked", description="A job was blocked (slicing failure or no eligible printer)."),
    EventDef("printer.alarm", description="A printer raised or escalated an alarm."),
    EventDef("spool.low", description="A spool fell below its low-stock threshold."),
)}


def event_catalog() -> dict[str, EventDef]:
    """Core events plus the events defined by registered plugins (a plugin cannot redefine a core name)."""
    from ..plugins import registered_plugins            # late: the plugin package imports this one's definitions
    out = dict(CORE_EVENTS)
    for m in registered_plugins():
        for d in m.defines_events:
            out.setdefault(d.name, d)
    return out


def definer_of_event(name: str) -> str | None:
    """The plugin that defines `name`, or None for a core (or unknown) event."""
    from ..plugins import registered_plugins
    return next((m.id for m in registered_plugins() if any(d.name == name for d in m.defines_events)), None)


def validate_publication(envelope: EventEnvelope, *, as_plugin: str | None = None) -> EventDef:
    """The envelope's event must exist, be published by its owner at its current schema version, and carry a valid payload."""
    d = event_catalog().get(envelope.name)
    if d is None:
        raise EventError(f"unknown event {envelope.name!r}")
    owner = "core" if envelope.name in CORE_EVENTS else definer_of_event(envelope.name)
    if owner is None:
        raise EventError(f"event {envelope.name!r} has no defining plugin")
    if envelope.source != owner:
        raise EventError(f"event {envelope.name!r} belongs to {owner!r}, not {envelope.source!r}")
    if (as_plugin or "core") != owner:
        raise EventError(f"{as_plugin or 'core'!r} may not publish {envelope.name!r} (owned by {owner!r})")
    if envelope.schema_version != d.version:
        raise EventError(f"{envelope.name} is at schema_version {d.version}, got {envelope.schema_version}")
    if d.payload_model is not None:
        try:
            d.payload_model.model_validate(envelope.payload)
        except Exception as e:
            fields = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in getattr(e, "errors", lambda: [])()) or str(e)
            raise EventError(f"{envelope.name}: invalid payload ({fields})") from None
    return d
