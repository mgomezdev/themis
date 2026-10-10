"""Plain declarations shared by the plugin manifest and the event hub (BIZ-249). No imports from `app.plugins`, so the manifest
can use them without a cycle. See docs/events.md."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

# `job.complete`, `acme_ntfy.sent`: lower-case dotted segments, at least two. A plugin-defined event starts with "<plugin id>.".
EVENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

# best_effort: in-memory, bounded, may be dropped when a subscriber falls behind or on a crash.
# durable:     written to the outbox in the publisher's own transaction and delivered at least once (retried until it succeeds).
Durability = Literal["best_effort", "durable"]


@dataclass(frozen=True)
class EventDef:
    """An event class: its name, schema version and delivery guarantee. `payload_model` (optional) validates `payload`."""
    name: str
    version: int = 1
    durability: Durability = "best_effort"
    description: str = ""
    payload_model: type[BaseModel] | None = None


@dataclass(frozen=True)
class EventSubscription:
    """A plugin's subscription: `handler` names an `async def handler(self, envelope)` on the plugin instance. The event may be
    core or defined by another plugin; if nothing defines it the subscription is simply dormant."""
    event: str
    handler: str
    timeout: float = 10.0
    queue_size: int = 1000
