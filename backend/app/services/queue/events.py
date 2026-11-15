"""What a job transition tells the outside world: the event bus, webhooks, notification channels and the browser broadcast.

Methods of `QueueEngine` (app/services/queue_engine.py), which inherits this mixin: `self` is the engine. Names that
module defines, and the collaborators tests replace there, are read as `_qe.<name>` at call time. See README.md here.
"""
from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ...models import (
    Job,
    Printer,
    Project,
    UploadedFile,
)
from ...plugins.capabilities.notify_channel import NOTIFY_CHANNEL
from ...eventing import EventEnvelope, EventError
from ...eventing.hub import hub as event_hub
from ...eventing.registry import CORE_EVENTS

import app.services.queue_engine as _qe


class EventsMixin:
    async def _publish_job_event(self, name: str, job_id: int, printer_id: int | None, origin: str | None, payload: dict, *, dedup: str) -> None:
        """Best-effort notice for a job transition (`job.failed` / `job.blocked`): core publishes, the origin says who reported it. A
        publication error is a bug in core, logged and never raised into the state change that already committed."""
        try:
            async with self._factory() as session:
                job = await session.get(Job, job_id)
            entities: dict[str, int | str] = {"job_id": job_id}
            if printer_id is not None:
                entities["printer_id"] = printer_id
            if job is not None and job.project_id is not None:
                entities["project_id"] = job.project_id
            if job is not None and job.order_id is not None:
                entities["order_id"] = job.order_id
            await event_hub.publish(EventEnvelope(name=name, schema_version=CORE_EVENTS[name].version, origin=origin or "core",
                                                  entities=entities, dedup_key=dedup, payload=payload))
        except EventError:
            _qe.logger.exception("Could not publish %s for job %s", name, job_id)

    async def _fire_webhooks(self, job_id: int, event: str, event_id: str | None = None) -> None:
        try:
            async with self._factory() as session:
                extra = await self._job_webhook_fields(session, job_id)
                await _qe.webhook_service.dispatch(session, event, job_id, extra or None, event_id=event_id)
        except Exception:
            _qe.logger.exception("Failed to dispatch webhooks for job %s", job_id)

    @staticmethod
    async def _job_webhook_fields(session: AsyncSession, job_id: int) -> dict:
        """What a companion app needs to correlate a job event with its own record: the project, its source app and external ref."""
        job = await session.get(Job, job_id)
        project = await session.get(Project, job.project_id) if job is not None and job.project_id is not None else None
        if project is None:
            return {}
        return {k: v for k, v in (("project_id", project.id), ("source_app", project.source_app),
                                  ("external_ref", project.external_ref)) if v is not None}

    async def _fire_notifications(
        self, job_id: int, event: str, printer_id: int | None = None, reason: str | None = None, event_id: str | None = None,
        origin: str | None = None,
    ) -> None:
        try:
            if not _qe.plugin_host.active_providers(NOTIFY_CHANNEL.id):
                return                                  # no notification channel is enabled: nothing to build
            async with self._factory() as session:
                job = await session.get(Job, job_id)
                if job is None:
                    return
                uploaded_file = await session.get(UploadedFile, job.uploaded_file_id)
                file_name = uploaded_file.original_filename if uploaded_file else f"job {job_id}"
                printer_name = None
                pid = printer_id if printer_id is not None else job.assigned_printer_id
                if pid is not None:
                    printer = await session.get(Printer, pid)
                    printer_name = printer.name if printer else None
            title, message = _qe._notification_content(event, job_id, file_name, printer_name, reason, origin)
            # Fire-and-forget, like _fire_webhooks/webhook_service.schedule: real
            # channel delivery is network/SMTP I/O with a 5s timeout per channel and
            # must not block the queue loop (this is awaited from _reconcile_printing_jobs,
            # which runs before new jobs are claimed each _process_queue iteration).
            # dispatch() already swallows every per-channel exception itself.
            self._spawn(_qe.notify(event, job_id, title, message, message_id=event_id), name=f"notify-{job_id}")
        except Exception:
            _qe.logger.exception("Failed to dispatch notifications for job %s", job_id)

    async def _broadcast_job(self, job_id: int | None) -> None:
        if not self._broadcast_cb or job_id is None:
            return
        try:
            async with self._factory() as session:
                job = await session.get(Job, job_id)
                if job:
                    await self._broadcast_cb("job_update", {
                        "id": job.id,
                        "status": job.status,
                        "assigned_printer_id": job.assigned_printer_id,
                        "queue_position": job.queue_position,
                        "project_id": job.project_id,
                    })
                # Full queue broadcast (active jobs only)
                result = await session.execute(
                    select(Job)
                    .where(Job.status.not_in(["complete", "cancelled"]))
                    .order_by(Job.queue_position.asc())
                )
                all_jobs = result.scalars().all()
                await self._broadcast_cb("queue_update", [
                    {"id": j.id, "status": j.status, "queue_position": j.queue_position}
                    for j in all_jobs
                ])
        except Exception:
            _qe.logger.exception("Failed to broadcast job update")
