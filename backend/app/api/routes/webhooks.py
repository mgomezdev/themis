"""Outbound webhook destinations (BIZ-172; docs/companion-apps.md). Each destination has its own URL, secret, event filter and enabled
flag. Scopes `settings:read` / `settings:write`. The secret is write-only (`has_secret` tells whether one is set)."""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...eventing.definitions import EVENT_NAME_RE
from ...models import WebhookDestination
from ...services import webhook_service
from ...services.webhook_service import now_iso as _now

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


def _check_url(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("url must be an http(s) URL")
    return v


def _check_events(v: list[str] | None) -> list[str] | None:
    if v is None:
        return None
    bad = [e for e in v if not EVENT_NAME_RE.match(e)]
    if bad:
        raise ValueError(f"not event names: {', '.join(bad)}")
    return list(dict.fromkeys(v))


class DestinationIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str | None = None
    secret: str | None = Field(default=None, max_length=256)
    events: list[str] = []                      # empty = every event
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def _valid_url(cls, v):
        return _check_url(v)

    @field_validator("events")
    @classmethod
    def _valid_events(cls, v):
        return _check_events(v) or []


class DestinationPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = None
    secret: str | None = Field(default=None, max_length=256)     # omitted = keep; "" = clear
    events: list[str] | None = None
    enabled: bool | None = None

    @field_validator("url")
    @classmethod
    def _valid_url(cls, v):
        return _check_url(v)

    @field_validator("events")
    @classmethod
    def _valid_events(cls, v):
        return _check_events(v)


def _out(d: WebhookDestination) -> dict:
    return {"id": d.id, "name": d.name, "url": d.url, "has_secret": d.secret is not None, "events": list(d.events or []),
            "enabled": bool(d.enabled), "created_at": d.created_at, "updated_at": d.updated_at, "last_attempt_at": d.last_attempt_at,
            "last_success_at": d.last_success_at, "last_status": d.last_status, "last_error": d.last_error}


async def _get_or_404(destination_id: int, session: AsyncSession) -> WebhookDestination:
    row = await session.get(WebhookDestination, destination_id)
    if row is None:
        raise HTTPException(404, f"Webhook destination {destination_id} not found")
    return row


@router.get("", summary="List webhook destinations", dependencies=[Depends(require_scope("settings:read"))])
async def list_destinations(session: AsyncSession = Depends(get_session)) -> list[dict]:
    return [_out(d) for d in (await session.execute(select(WebhookDestination).order_by(WebhookDestination.id))).scalars()]


@router.post("", status_code=201, summary="Create a webhook destination",
             responses={409: {"description": "A destination with this name exists"}},
             dependencies=[Depends(require_scope("settings:write"))])
async def create_destination(body: DestinationIn, session: AsyncSession = Depends(get_session)) -> dict:
    now = _now()
    row = WebhookDestination(name=body.name, url=body.url, secret=body.secret or None, events=body.events, enabled=body.enabled,
                             created_at=now, updated_at=now)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, f"A webhook destination named {body.name!r} already exists") from None
    await session.refresh(row)
    return _out(row)


@router.get("/{destination_id}", summary="Get a webhook destination", responses={404: {"description": "Not found"}},
            dependencies=[Depends(require_scope("settings:read"))])
async def get_destination(destination_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    return _out(await _get_or_404(destination_id, session))


@router.patch("/{destination_id}", summary="Update a webhook destination",
              responses={404: {"description": "Not found"}, 409: {"description": "Name already used"}},
              dependencies=[Depends(require_scope("settings:write"))])
async def patch_destination(destination_id: int, body: DestinationPatch, session: AsyncSession = Depends(get_session)) -> dict:
    row = await _get_or_404(destination_id, session)
    fields = body.model_fields_set
    if "name" in fields and body.name is not None:
        row.name = body.name
    if "url" in fields:
        row.url = body.url
    if "secret" in fields and body.secret is not None:
        row.secret = body.secret or None
    if "events" in fields and body.events is not None:
        row.events = body.events
    if "enabled" in fields and body.enabled is not None:
        row.enabled = body.enabled
    row.updated_at = _now()
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, f"A webhook destination named {body.name!r} already exists") from None
    await session.refresh(row)
    return _out(row)


@router.delete("/{destination_id}", status_code=204, summary="Delete a webhook destination",
               responses={404: {"description": "Not found"}}, dependencies=[Depends(require_scope("settings:write"))])
async def delete_destination(destination_id: int, session: AsyncSession = Depends(get_session)) -> None:
    await session.delete(await _get_or_404(destination_id, session))
    await session.commit()


@router.post("/{destination_id}/test", summary="Send a test event to a destination",
             responses={404: {"description": "Not found"}, 422: {"description": "The destination has no URL"}},
             dependencies=[Depends(require_scope("settings:write"))])
async def test_destination(destination_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """One signed `webhook.test` POST (no retries), whatever the destination's filter or enabled flag. Returns what the receiver said."""
    row = await _get_or_404(destination_id, session)
    if not row.url:
        raise HTTPException(422, "This destination has no URL")
    outcome = await webhook_service.attempt(row.url, row.secret, webhook_service.build_payload("webhook.test", None, {"destination": row.name}))
    row.last_attempt_at, row.last_status, row.last_error = _now(), outcome.status, outcome.error
    if outcome.delivered:
        row.last_success_at = row.last_attempt_at
    await session.commit()
    return {"ok": outcome.delivered, "status": outcome.status, "error": outcome.error}
