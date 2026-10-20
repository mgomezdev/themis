"""Payload schemas of core events (BIZ-269). Kept apart from the registry so consumers can import them without a cycle."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class InventoryUse(BaseModel):
    """Legacy: a `job.complete` written before inventory subscribed on its own carried the resolved deduction."""
    model_config = ConfigDict(extra="forbid")
    spool_ref: str
    grams: float


class JobStartedPayload(BaseModel):
    """`job.started`: a printer began a job's physical print. `loaded_filaments` is the printer's filament slots at that moment
    (what is loaded where), for observers that need to know what the print used; the event says nothing about inventory."""
    model_config = ConfigDict(extra="forbid")
    loaded_filaments: list[dict] = []


class JobCompletePayload(BaseModel):
    """`job.complete`: `source` is `queue` (a real print finished) or `manual` (an admin completed it without printing, which
    fires no webhooks or notifications). `loaded_filaments` is the printer's filament slots at completion (the loaded slot may change
    once the print is over, so it travels with the event). Observers, inventory among them, work out the rest themselves.
    `inventory` / `deduction_notes` are the legacy shape of an event still waiting from before that; nothing writes them now."""
    model_config = ConfigDict(extra="forbid")
    source: Literal["queue", "manual"]
    actual_seconds: int | None = None
    actual_grams: float | None = None
    loaded_filaments: list[dict] = []
    inventory: InventoryUse | list[InventoryUse] | None = None
    deduction_notes: list[str] = []


class ProjectPayload(BaseModel):
    """`project.created` / `project.generated` / `project.stage_changed` (schema v1). `source_app` + `external_ref` are the companion
    app's own key for the project (null for projects made in the UI). `job_ids` is set for `project.generated`; `previous_stage`
    for `project.stage_changed`."""
    model_config = ConfigDict(extra="forbid")
    name: str
    stage: str
    source_app: str | None = None
    external_ref: str | None = None
    job_ids: list[int] | None = None
    previous_stage: str | None = None
