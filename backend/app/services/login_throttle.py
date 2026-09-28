"""Per-client-IP throttle for failed sign-in / recovery attempts (in-memory, per process).

Enough to stop online password guessing and PBKDF2 CPU drain from one address; state resets on
restart, which is fine for a single-container app."""
from __future__ import annotations
import time
from collections import defaultdict, deque

from fastapi import HTTPException

MAX_FAILURES = 10
WINDOW_SECONDS = 15 * 60

_failures: dict[str, deque[float]] = defaultdict(deque)


def _prune(q: deque[float], now: float) -> None:
    while q and q[0] <= now - WINDOW_SECONDS:
        q.popleft()


def check(ip: str | None) -> None:
    """Raise 429 if this address has too many recent failures."""
    q = _failures[ip or "?"]
    _prune(q, time.monotonic())
    if len(q) >= MAX_FAILURES:
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")


def record_failure(ip: str | None) -> None:
    _failures[ip or "?"].append(time.monotonic())


def reset() -> None:
    _failures.clear()
