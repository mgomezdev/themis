"""Payload schemas of core events (BIZ-269). Kept apart from the registry so consumers can import them without a cycle."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class InventoryUse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spool_ref: str          # resolved when the job completed: the spool in the slot that printed it
    grams: float


class JobCompletePayload(BaseModel):
    """`job.complete`: `source` is `queue` (a real print finished) or `manual` (an admin completed it without printing, which
    fires no webhooks or notifications). `inventory` is null when no spool deduction applies."""
    model_config = ConfigDict(extra="forbid")
    source: Literal["queue", "manual"]
    actual_seconds: int | None = None
    actual_grams: float | None = None
    inventory: InventoryUse | None = None
