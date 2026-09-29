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
