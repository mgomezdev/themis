"""Idempotency keys for mutating API requests (BIZ-172; docs/companion-apps.md).

A client sends `Idempotency-Key: <opaque string>` with a POST it may have to repeat. The first request claims the key (committed
before any work starts), runs, and stores its response; a repeat with the same key and the same request returns that stored response
(header `Idempotent-Replay: true`) instead of doing the work again. A repeat while the first is still running is 409; the same key with a
different request is 422. If the first request failed *before changing anything*, its claim is released so the retry runs for real; if it
failed after committing part of its work (e.g. some of a project's jobs) the key is marked `partial` and a retry gets 409 with an
explanation instead of repeating that work. A claim older than STALE_AFTER is treated as abandoned (the process died) and may be taken
over (once) — unless the caller's `committed_since` check finds work the first attempt committed (then the key turns `partial`
and the retry gets 409, as above). Finished keys are kept KEEP_FOR. Keys are scoped to the endpoint."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import IdempotencyKey

STALE_AFTER = timedelta(minutes=15)
KEEP_FOR = timedelta(hours=24)
MAX_KEY_LEN = 200


@dataclass(frozen=True)
class Replay:
    status_code: int
    response: dict


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def request_hash(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def new_owner() -> str:
    """A token naming the request that claims a key: pass it to `claim` and to `release`, so a request whose claim was taken
    over (it ran past STALE_AFTER) cannot free the new holder's claim."""
    return uuid.uuid4().hex


def check_key(key: str) -> str:
    key = key.strip()
    if not key or len(key) > MAX_KEY_LEN:
        raise HTTPException(422, f"Idempotency-Key must be 1-{MAX_KEY_LEN} characters")
    return key


async def claim(factory: async_sessionmaker[AsyncSession], scope: str, key: str, req_hash: str, *, baseline: dict | None = None,
                committed_since: Callable[[dict], Awaitable[str | None]] | None = None, owner: str | None = None) -> Replay | None:
    """None: this request owns the key and must run, then `complete` (or `release` on failure). A `Replay`: answer with it.
    `baseline` is stored with a fresh claim; before a stale claim is taken over, `committed_since(baseline)` may return a detail
    saying the first attempt already committed work — the key is then marked `partial` and this request gets 409."""
    key = check_key(key)
    now = datetime.now(timezone.utc)
    async with factory() as session:
        await session.execute(delete(IdempotencyKey).where(IdempotencyKey.state.in_(("done", "partial")), IdempotencyKey.created_at < _iso(now - KEEP_FOR)))
        inserted = (await session.execute(
            sqlite_insert(IdempotencyKey).values(scope=scope, key=key, request_hash=req_hash, state="in_progress", created_at=_iso(now),
                                                 response=baseline, claim_token=owner)
            .on_conflict_do_nothing().returning(IdempotencyKey.id))).scalar_one_or_none()
        if inserted is not None:
            await session.commit()
            return None
        row = (await session.execute(select(IdempotencyKey).where(IdempotencyKey.scope == scope, IdempotencyKey.key == key))).scalar_one()
        if row.request_hash != req_hash:
            await session.commit()
            raise HTTPException(422, "This Idempotency-Key was already used with a different request")
        if row.state == "done":
            await session.commit()
            return Replay(row.status_code or 200, row.response or {})
        if row.state == "partial":
            await session.commit()
            raise HTTPException(409, (row.response or {}).get("detail") or "An earlier request with this Idempotency-Key partly completed")
        if row.created_at < _iso(now - STALE_AFTER):                     # the first attempt died: take the key over (once)
            await session.commit()
            done = await committed_since(row.response or {}) if committed_since is not None else None
            same_claim = (IdempotencyKey.id == row.id, IdempotencyKey.created_at == row.created_at, IdempotencyKey.state == "in_progress")
            if done:                                                     # it got far enough to commit work: never repeat it
                await session.execute(update(IdempotencyKey).where(*same_claim)
                                      .values(state="partial", status_code=None, response={"detail": done}))
                await session.commit()
                raise HTTPException(409, done)
            taken = await session.execute(update(IdempotencyKey).where(*same_claim).values(created_at=_iso(now), claim_token=owner))
            await session.commit()
            if taken.rowcount == 1:
                return None
            raise HTTPException(409, "A request with this Idempotency-Key is still being processed")
        await session.commit()
    raise HTTPException(409, "A request with this Idempotency-Key is still being processed")


async def complete(factory: async_sessionmaker[AsyncSession], scope: str, key: str, status_code: int, response: dict) -> None:
    """Store the response of a request that did its work. Whoever finishes first wins while the key is in progress, even a
    request whose claim was taken over: committed work outranks a claim still running (the retry then replays it)."""
    async with factory() as session:
        await session.execute(update(IdempotencyKey).where(IdempotencyKey.scope == scope, IdempotencyKey.key == check_key(key),
                                                           IdempotencyKey.state == "in_progress")
                              .values(state="done", status_code=status_code, response=response))
        await session.commit()


async def partial(factory: async_sessionmaker[AsyncSession], scope: str, key: str, detail: str) -> None:
    """The request failed after committing part of its work: keep the key so a retry cannot repeat that work. Like
    `complete`, any request with committed work may set it while the key is in progress; a stored response is never
    overwritten."""
    async with factory() as session:
        await session.execute(update(IdempotencyKey).where(IdempotencyKey.scope == scope, IdempotencyKey.key == check_key(key),
                                                           IdempotencyKey.state == "in_progress")
                              .values(state="partial", status_code=None, response={"detail": detail}))
        await session.commit()


async def release(factory: async_sessionmaker[AsyncSession], scope: str, key: str, *, owner: str | None) -> None:
    """A request that changed nothing gives its claim back. Only the claim it still holds (GH #246): a request whose claim
    was taken over must not free the new holder's, or a third request would run the work while the retry still is. `owner`
    is required (pass the token given to `claim`; None only for a claim made without one)."""
    async with factory() as session:
        await session.execute(delete(IdempotencyKey).where(
            IdempotencyKey.scope == scope, IdempotencyKey.key == check_key(key), IdempotencyKey.state == "in_progress",
            IdempotencyKey.claim_token.is_(None) if owner is None else IdempotencyKey.claim_token == owner))
        await session.commit()
