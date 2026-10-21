"""The plugin -> core callback for job events (docs/superpowers/specs/2026-10-20-job-events-and-details-design.md §2).

A printer plugin never publishes core events or touches job rows. It tells core what happened through its own `JobReporter`
(`ctx.job_reporter.report(job_id, kind, details=None)`); core checks that the job is on one of that plugin's printers, applies the
state change with the queue's atomic claim, stores `details` in the SAME transaction, and publishes the event with the plugin as its
`origin`. A repeat is a no-op (the loser of the claim publishes nothing), and nothing here ever releases a job from its printer.

`kind`: `started` (the printer began the physical print), `complete`, `failed` (a started print ended in failure) and `blocked`
(a started print is halted: a notice only, the job stays `printing` on its printer)."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from sqlalchemy import select

from ..models import Job, Printer
from . import job_details

KINDS = ("started", "complete", "failed", "blocked")


class JobReportError(Exception):
    """The report cannot be accepted: unknown job or kind, a job on another plugin's printer, a disabled plugin, a wrong state."""


class JobReporter:
    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id

    async def _job_and_printer(self, job_id: int) -> tuple[Job, int]:
        from ..eventing.hub import hub
        from ..plugins.host import plugin_host
        if not plugin_host.is_enabled(self.plugin_id):
            raise JobReportError(f"plugin {self.plugin_id!r} is not enabled")
        async with hub.session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                raise JobReportError(f"job {job_id} does not exist")
            printer_id = job.assigned_printer_id if job.assigned_printer_id is not None else job.printed_on_printer_id
            printer = await session.get(Printer, printer_id) if printer_id is not None else None
            if printer is None or printer.plugin_id != self.plugin_id:
                raise JobReportError(f"job {job_id} is not on a printer of plugin {self.plugin_id!r}")
            return job, printer.id

    async def report(self, job_id: int, kind: str, details: Mapping[str, Any] | None = None, *, reason: str | None = None) -> bool:
        """Report `kind` for `job_id`. Returns True when it changed something, False for a duplicate or a job that is not in a state
        the report applies to (nothing is written or published then). Raises `JobReportError` for a report core must not accept."""
        from .queue_engine import queue_engine
        if kind not in KINDS:
            raise JobReportError(f"unknown job event kind {kind!r}; expected one of {', '.join(KINDS)}")
        job, printer_id = await self._job_and_printer(job_id)
        origin = self.plugin_id
        if kind == "complete":
            return await queue_engine.complete_job(job_id, printer_id, origin=origin, details=details)
        if kind == "started":
            return await queue_engine.mark_printing(job_id, printer_id, origin=origin, details=details,
                                                    only_from=("slicing", "sliced", "uploading"))
        if kind == "failed":
            return await queue_engine.fail_started_job(job_id, printer_id, origin=origin, details=details, fire_notices=True,
                                                       reason=reason or "the printer reported the print failed")
        return await queue_engine.note_print_blocked(job_id, printer_id, origin=origin, details=details,
                                                     reason=reason or "the printer halted the print")


class JobDetailsReader:
    """Read-only `ctx.job_details`: `await ctx.job_details.get(job_id, ["filament_usage"])`."""
    async def get(self, job_id: int, fields: Sequence[str], origin: str | None = None) -> dict[str, job_details.DetailResult]:
        return await job_details.get_details(job_id, fields, origin)


class JobDetailsWriter:
    """`ctx.job_details_writer`: a plugin adds fields to a job (its own, e.g. `filament.cost`) in its own transaction. The origin is
    always the writing plugin; a disabled plugin cannot write. One writer per field is the rule, not yet enforced."""
    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id

    async def set(self, job_id: int, details: Mapping[str, Any]) -> None:
        from ..eventing.hub import hub
        from ..plugins.host import plugin_host
        if not plugin_host.is_enabled(self.plugin_id):
            raise JobReportError(f"plugin {self.plugin_id!r} is not enabled")
        async with hub.session_factory() as session:
            if await session.get(Job, job_id) is None:
                raise JobReportError(f"job {job_id} does not exist")
            await job_details.set_details(session, job_id, self.plugin_id, details)
            await session.commit()
