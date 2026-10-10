"""Project lifecycle events (BIZ-172): published on the event hub after the route's commit, forwarded to webhook destinations.

`publish` never raises (a failing subscriber or a bug here must not fail the API call). The `webhooks.project_events` subscriber turns each
envelope into the v1 webhook payload (docs/companion-apps.md): event, event_id (= envelope id), schema_version, timestamp, project_id,
name, stage, source_app, external_ref, plus job_ids / previous_stage where they apply."""
from __future__ import annotations

import logging

from ..eventing import EventEnvelope
from ..eventing.hub import hub
from ..models import Project
from . import webhook_service

logger = logging.getLogger("app")

EVENTS = ("project.created", "project.generated", "project.stage_changed")


async def publish(name: str, project: Project, *, job_ids: list[int] | None = None, previous_stage: str | None = None,
                  dedup_key: str | None = None) -> None:
    try:
        entities: dict[str, int | str] = {"project_id": project.id}
        if project.customer_id is not None:
            entities["customer_id"] = project.customer_id
        if project.order_id is not None:
            entities["order_id"] = project.order_id
        payload = {"name": project.name, "stage": project.stage, "source_app": project.source_app, "external_ref": project.external_ref,
                   "job_ids": job_ids, "previous_stage": previous_stage}
        await hub.publish(EventEnvelope(name=name, entities=entities, dedup_key=dedup_key, payload=payload))
    except Exception:
        logger.exception("Could not publish %s for project %s", name, getattr(project, "id", None))


def webhook_body(envelope: EventEnvelope) -> dict:
    body = {"project_id": envelope.entities.get("project_id")}
    body.update({k: v for k, v in envelope.payload.items() if v is not None})
    body["occurred_at"] = envelope.occurred_at
    return body


async def forward_to_webhooks(envelope: EventEnvelope) -> None:
    async with hub.session_factory() as session:
        await webhook_service.dispatch(session, envelope.name, None, webhook_body(envelope), event_id=envelope.id)


def register() -> None:
    for event in EVENTS:
        name = f"webhooks.{event}"
        if not hub.has_subscriber(name):
            hub.subscribe(event, forward_to_webhooks, name=name, timeout=30.0)


register()
