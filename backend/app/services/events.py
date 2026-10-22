"""In-process event bus (BIZ-249 minimal slice). Publishers never wait for subscribers: each handler runs as its own task,
bounded by a timeout, and its failure is logged. Ordering across handlers is not guaranteed.

Durability, versioned envelopes and plugin-declared subscriptions are out of scope here (BIZ-249)."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger("app")


class Event(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)


E = TypeVar("E", bound=Event)
Handler = Callable[[Event], Awaitable[None]]
_DEFAULT_TIMEOUT: Any = object()       # "use the bus's handler_timeout"; an explicit None means no limit


class EventBus:
    def __init__(self, handler_timeout: float = 10.0) -> None:
        self._handler_timeout = handler_timeout
        self._subscribers: list[tuple[type[Event], Handler, Any]] = []
        self._tasks: set[asyncio.Task] = set()

    def subscribe(self, event_type: type[E], handler: Callable[[E], Awaitable[None]], *, timeout: Any = _DEFAULT_TIMEOUT) -> None:
        """Deliver events of `event_type` (or any subclass) to `handler`. `timeout` bounds one invocation: by default the bus's
        `handler_timeout`; a float overrides it; None means no limit (for a handler whose work must not be cut short, e.g.
        completing a job)."""
        self._subscribers.append((event_type, handler, timeout))  # type: ignore[arg-type]

    async def publish(self, event: Event) -> None:
        """Schedule every matching handler and return without waiting for them."""
        for event_type, handler, timeout in list(self._subscribers):
            if isinstance(event, event_type):
                task = asyncio.ensure_future(self._run(handler, event, timeout))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def drain(self, timeout: float = 5.0) -> None:
        """Wait until every handler scheduled so far has finished (tests, shutdown)."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout)

    async def _run(self, handler: Handler, event: Event, timeout: Any = _DEFAULT_TIMEOUT) -> None:
        name = getattr(handler, "__qualname__", repr(handler))
        limit = self._handler_timeout if timeout is _DEFAULT_TIMEOUT else timeout
        try:
            await asyncio.wait_for(handler(event), timeout=limit)
        except asyncio.TimeoutError:
            logger.warning("Event handler %s timed out on %s", name, type(event).__name__)
        except Exception:
            logger.exception("Event handler %s failed on %s", name, type(event).__name__)


event_bus = EventBus()
