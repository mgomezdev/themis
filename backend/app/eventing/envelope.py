"""The neutral event envelope (BIZ-249). Identical for core and plugin events; consumers read only these fields."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .definitions import EVENT_NAME_RE

MAX_PAYLOAD_BYTES = 16 * 1024            # an envelope is a notice, not a data transfer: fetch big things by id
ENTITY_KEYS = frozenset({"job_id", "project_id", "printer_id", "order_id", "customer_id", "file_id", "spool_ref"})


class EventError(ValueError):
    """An envelope or publication is malformed (unknown event, wrong source, bad payload, wrong durability path)."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class EventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)       # unique per publication
    name: str                                                      # versioned class name, e.g. "job.complete"
    schema_version: int = 1
    occurred_at: str = Field(default_factory=_now)                  # UTC ISO-8601, when the transition committed
    source: str = "core"                                            # "core" or the publishing plugin's id
    # Entity references (ids of the things the event is about); values are int or str. Only ENTITY_KEYS are allowed.
    entities: dict[str, int | str] = Field(default_factory=dict)
    # Logical identity: two publications with the same dedup_key are the same logical event (e.g. "job.complete:42").
    dedup_key: str | None = None
    correlation_id: str | None = None                               # ties events of one request/flow together
    payload: dict[str, Any] = Field(default_factory=dict)           # JSON-serialisable, <= MAX_PAYLOAD_BYTES

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not EVENT_NAME_RE.match(v):
            raise ValueError(f"{v!r} is not an event name (dotted lower-case segments)")
        return v

    @field_validator("schema_version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v < 1:
            raise ValueError("schema_version starts at 1")
        return v

    @field_validator("source")
    @classmethod
    def _source(cls, v: str) -> str:
        if not v or len(v) > 64:
            raise ValueError("source must be 'core' or a plugin id")
        return v

    @field_validator("entities")
    @classmethod
    def _entities(cls, v: dict) -> dict:
        bad = sorted(set(v) - ENTITY_KEYS)
        if bad:
            raise ValueError(f"unknown entity reference(s) {bad}; allowed: {sorted(ENTITY_KEYS)}")
        return v

    @field_validator("payload")
    @classmethod
    def _payload(cls, v: dict) -> dict:
        try:
            size = len(json.dumps(v, separators=(",", ":")).encode())
        except (TypeError, ValueError) as e:
            raise ValueError(f"payload must be JSON-serialisable: {e}") from None
        if size > MAX_PAYLOAD_BYTES:
            raise ValueError(f"payload is {size} bytes; the limit is {MAX_PAYLOAD_BYTES}")
        return v
