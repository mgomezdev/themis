"""Tracked fire-and-forget tasks for inventory work that must stay off the queue loop's critical path (snapshots, outbox
flushes). `spawn` is the single entry point so tests can intercept it and `drain` can wait for everything to settle."""
from __future__ import annotations

import asyncio
import logging
from typing import Coroutine

logger = logging.getLogger("app")

_tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine, name: str | None = None) -> asyncio.Task:
    task = asyncio.create_task(_guard(coro), name=name)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def _guard(coro: Coroutine) -> None:
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Inventory background task failed")


async def drain() -> None:
    """Wait for every spawned task (tests, shutdown)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
