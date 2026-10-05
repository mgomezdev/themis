from __future__ import annotations

from pydantic import BaseModel, Field


class LocalInventorySettings(BaseModel):
    default_initial_g: float = Field(
        default=1000, ge=0, le=100_000,
        title="Default spool weight (g)",
        description="Weight a new spool is created with when none is given.")
