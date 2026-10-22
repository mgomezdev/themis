"""Polling helper for tests that wait on background asyncio work (replaces fixed sleeps)."""
import asyncio
import inspect
import time


async def wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.01, what: str = "condition"):
    """Poll `predicate` (sync or async callable) until it returns truthy; return that value.

    Fails with a descriptive TimeoutError instead of asserting on a state that a slow machine
    (or coverage tracing) hasn't reached yet.
    """
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return result
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(interval)


async def settle_background_tasks(*, timeout: float = 5.0) -> None:
    """Wait until every asyncio task other than the caller has finished.

    For code under test that fires background work with a bare `asyncio.create_task` (no handle to
    await), e.g. the queue engine's slice/upload/print tasks. Deterministic for negative cases too:
    "no task was spawned" settles immediately, so an assertion that something did NOT happen no
    longer depends on how long a fixed sleep was.
    """
    current = asyncio.current_task()
    deadline = time.monotonic() + timeout
    while True:
        pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
        if not pending:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"{len(pending)} background task(s) still running after {timeout}s: {pending}")
        await asyncio.wait(pending, timeout=remaining)


async def settle_events(factory) -> None:
    """Deliver every due durable event (e.g. a job's `job.complete` effects) and the inventory work it spawns, then return.

    Production delivers these from the dispatcher loop shortly after the completion commits; tests that assert on the effects
    call this once instead of racing it."""
    from app.eventing.hub import hub
    from app.services.inventory import tasks as inventory_tasks
    await hub.deliver_pending(factory)
    await inventory_tasks.drain()
    await hub.deliver_pending(factory)                  # a delivery can enqueue work that is itself due
