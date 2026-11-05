"""The plugin -> core job callback (spec §2): ownership, an atomic claim, details in the same transaction as the state change, the event's
origin, and no release from the printer. Real QueueEngine + PrinterManager + the test DB; only the printer client is absent."""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.eventing.hub import hub
from app.models import EventOutbox, GcodeFile, Job, JobDetail, Printer, UploadedFile
from app.plugins import JobFieldDef
from app.plugins.host import plugin_host
from app.plugins.manifest import HOST_API, PluginManifest
from app.services import job_details as jd
from app.services import queue_engine as qe_module
from app.services.job_reporter import JobDetailsWriter, JobReportError, JobReporter
from app.services.printer_manager import PrinterManager
from app.services.queue_engine import QueueEngine
from tests.eventing.fakes import register
from tests.plugins.dummy_plugin import DummyProvider, DummySettings

NOW = "2026-10-20T12:00:00Z"


def manifest(pid: str) -> PluginManifest:
    return PluginManifest(id=pid, name=pid, version="1.0.0", host_api=HOST_API, settings_model=DummySettings, factory=DummyProvider,
                          job_fields=(JobFieldDef("print_end_time"), JobFieldDef("result"), JobFieldDef("filament_usage")))


@pytest.fixture
async def env(session_factory, monkeypatch, tmp_path):
    register(manifest("prn_a"), manifest("prn_b"))
    await plugin_host.update_config("prn_a", enabled=True)
    await plugin_host.update_config("prn_b", enabled=True)
    mgr = PrinterManager()
    engine = QueueEngine(session_factory, mgr, MagicMock(), broadcast_cb=None)
    monkeypatch.setattr(qe_module, "queue_engine", engine)
    # the engine fires webhooks/notifications; keep them off the network
    monkeypatch.setattr(engine, "_fire_webhooks", _noop)
    monkeypatch.setattr(engine, "_fire_notifications", _noop)
    yield MagicMock(engine=engine, mgr=mgr, tmp=tmp_path)
    engine._executor.shutdown(wait=False)


async def _noop(*a, **k):
    return None


async def seed(session_factory, tmp_path, *, plugin="prn_a", status="printing", assigned=True) -> tuple[int, int]:
    """A printer of `plugin` and a job in `status` on it, with a parked gcode file."""
    gcode = tmp_path / "j.gcode"
    gcode.write_text("G28\n")
    async with session_factory() as s:
        printer = Printer(name="P", printer_type="bambu", connection_config={}, orca_printer_profiles=[], plugin_id=plugin)
        f = UploadedFile(original_filename="m.3mf", stored_path="/x", plates=[], uploaded_at=NOW)
        s.add_all([printer, f])
        await s.flush()
        job = Job(uploaded_file_id=f.id, plate_number=1, queue_position=1.0, status=status, created_at=NOW, updated_at=NOW,
                  assigned_printer_id=printer.id if assigned else None, printed_on_printer_id=printer.id,
                  actual_seconds=600, actual_filament_grams=12.5)
        s.add(job)
        await s.flush()
        s.add(GcodeFile(job_id=job.id, printer_id=printer.id, path=str(gcode)))
        await s.commit()
        return job.id, printer.id


async def rows(session_factory, model):
    async with session_factory() as s:
        return list((await s.execute(select(model))).scalars())


async def test_a_plugin_reporting_on_another_plugins_job_is_rejected_and_nothing_changes(env, session_factory):
    job_id, _ = await seed(session_factory, env.tmp, plugin="prn_a")
    with pytest.raises(JobReportError, match="not on a printer of plugin 'prn_b'"):
        await JobReporter("prn_b").report(job_id, "complete")
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        assert job.status == "printing" and job.completed_at is None
    assert await rows(session_factory, EventOutbox) == [] and await rows(session_factory, JobDetail) == []


async def test_an_unknown_job_kind_or_a_disabled_plugin_is_rejected(env, session_factory):
    job_id, _ = await seed(session_factory, env.tmp)
    with pytest.raises(JobReportError, match="does not exist"):
        await JobReporter("prn_a").report(999999, "complete")
    with pytest.raises(JobReportError, match="unknown job event kind"):
        await JobReporter("prn_a").report(job_id, "exploded")
    await plugin_host.update_config("prn_a", enabled=False)
    with pytest.raises(JobReportError, match="not enabled"):
        await JobReporter("prn_a").report(job_id, "complete")
    assert (await rows(session_factory, Job))[0].status == "printing"


async def test_complete_stores_details_and_one_durable_event_with_the_plugin_as_origin(env, session_factory):
    job_id, printer_id = await seed(session_factory, env.tmp)

    assert await JobReporter("prn_a").report(job_id, "complete", {"filament_usage": {"grams": 9.0, "breakdown": None}}) is True

    async with session_factory() as s:
        job = await s.get(Job, job_id)
        assert job.status == "complete" and job.completed_at is not None and job.assigned_printer_id == printer_id
        assert (await s.get(Printer, printer_id)).awaiting_plate_clear is True          # the plate gate stays set after a reporter-driven end
    assert env.mgr.is_awaiting_plate_clear(printer_id) is True
    (outbox,) = await rows(session_factory, EventOutbox)
    assert outbox.name == "job.complete" and outbox.envelope["origin"] == "prn_a" and outbox.envelope["schema_version"] == 2
    got = await jd.get_details(job_id, ["result", "print_end_time", "filament_usage", "print_seconds"])
    assert got["result"]["value"] == "success" and got["result"]["origin"] == "prn_a"
    assert got["filament_usage"]["value"] == {"grams": 9.0, "breakdown": None}          # the plugin's value wins over core's default
    assert got["print_end_time"]["status"] == "ok" and got["print_seconds"]["value"] == 600
    assert await rows(session_factory, GcodeFile) == []


async def test_a_duplicate_complete_is_a_no_op_with_exactly_one_outbox_row(env, session_factory):
    job_id, _ = await seed(session_factory, env.tmp)
    reporter = JobReporter("prn_a")

    assert await reporter.report(job_id, "complete") is True
    assert await reporter.report(job_id, "complete") is False

    assert len(await rows(session_factory, EventOutbox)) == 1
    assert len([r for r in await rows(session_factory, JobDetail) if r.field == "result"]) == 1


async def test_details_and_the_state_change_roll_back_together_on_a_forced_failure(env, session_factory, monkeypatch):
    job_id, _ = await seed(session_factory, env.tmp)

    async def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(jd, "set_details", boom)
    with pytest.raises(RuntimeError):
        await JobReporter("prn_a").report(job_id, "complete")

    async with session_factory() as s:
        assert (await s.get(Job, job_id)).status == "printing"              # no `complete` row without its details
    assert await rows(session_factory, EventOutbox) == [] and len(await rows(session_factory, GcodeFile)) == 1


async def test_the_core_adapter_for_a_printer_completion_carries_the_printers_plugin_as_origin(env, session_factory):
    job_id, printer_id = await seed(session_factory, env.tmp)
    await env.engine.handle_print_complete(printer_id)
    (outbox,) = await rows(session_factory, EventOutbox)
    assert outbox.envelope["origin"] == "prn_a"


async def test_a_manual_completion_carries_origin_core(env, session_factory):
    """An admin's manual completion (`POST /jobs/{id}/complete-manually`) writes the event with core as its origin."""
    from app.services import completion_events
    job_id, printer_id = await seed(session_factory, env.tmp)
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        await completion_events.enqueue_job_complete(s, job, printer_id, source="manual")
        await s.commit()
    (outbox,) = await rows(session_factory, EventOutbox)
    assert outbox.envelope["origin"] == "core" and outbox.envelope["payload"]["source"] == "manual"


async def test_started_claims_a_pre_start_job_once_and_writes_the_start_event_and_detail(env, session_factory):
    job_id, printer_id = await seed(session_factory, env.tmp, status="uploading")
    reporter = JobReporter("prn_a")

    assert await reporter.report(job_id, "started") is True
    assert await reporter.report(job_id, "started") is False                # already printing

    async with session_factory() as s:
        job = await s.get(Job, job_id)
        assert job.status == "printing" and job.printed_on_printer_id == printer_id
        assert (await s.get(Printer, printer_id)).awaiting_plate_clear is True
    (outbox,) = await rows(session_factory, EventOutbox)
    assert outbox.name == "job.started" and outbox.envelope["origin"] == "prn_a"
    assert (await jd.get_details(job_id, ["print_start_time"]))["print_start_time"]["status"] == "ok"


async def test_failed_ends_a_started_print_keeps_the_printer_and_publishes_job_failed(env, session_factory, listen):
    job_id, printer_id = await seed(session_factory, env.tmp)
    seen = listen("job.failed")
    assert await JobReporter("prn_a").report(job_id, "failed", reason="nozzle clog") is True
    await hub.drain()

    async with session_factory() as s:
        job = await s.get(Job, job_id)
        assert (job.status, job.block_reason, job.assigned_printer_id, job.deduction_skipped) == ("failed", "nozzle clog", printer_id, True)
    assert await rows(session_factory, EventOutbox) == []                    # job.failed is best-effort: no outbox row, and no deduction event
    assert [(e.origin, e.payload["reason"], e.payload["phase"]) for e in seen] == [("prn_a", "nozzle clog", "print")]
    assert (await jd.get_details(job_id, ["result"]))["result"]["value"] == "failure"
    assert await JobReporter("prn_a").report(job_id, "failed") is False      # a repeat does nothing


async def test_a_mid_print_blocked_is_a_notice_only_that_never_releases_the_printer(env, session_factory, listen):
    job_id, printer_id = await seed(session_factory, env.tmp)
    seen = listen("job.blocked")
    reporter = JobReporter("prn_a")

    assert await reporter.report(job_id, "blocked", reason="filament runout") is True
    assert await reporter.report(job_id, "blocked", reason="filament runout") is False          # same reason: one notice
    assert await reporter.report(job_id, "blocked", reason="nozzle jam") is True                # a different reason is a new notice
    await hub.drain()

    async with session_factory() as s:
        job = await s.get(Job, job_id)
        assert (job.status, job.assigned_printer_id) == ("printing", printer_id)               # not blocked, not released
    assert [(e.origin, e.payload) for e in seen] == [("prn_a", {"reason": "filament runout", "phase": "print"}),
                                                     ("prn_a", {"reason": "nozzle jam", "phase": "print"})]
    assert (await jd.get_details(job_id, ["blocked_reason"]))["blocked_reason"]["value"] == "nozzle jam"

    assert await reporter.report(job_id, "complete") is True                                   # the print ends: the stale reason goes
    assert (await jd.get_details(job_id, ["blocked_reason"]))["blocked_reason"]["status"] != "ok"


async def test_blocked_for_a_job_that_is_not_printing_is_ignored(env, session_factory):
    job_id, _ = await seed(session_factory, env.tmp, status="uploading")
    assert await JobReporter("prn_a").report(job_id, "blocked", reason="x") is False
    assert (await rows(session_factory, Job))[0].status == "uploading" and await rows(session_factory, JobDetail) == []


async def test_a_reporter_never_changes_the_assignment(env, session_factory):
    job_id, printer_id = await seed(session_factory, env.tmp)
    await JobReporter("prn_a").report(job_id, "blocked", reason="r")
    await JobReporter("prn_a").report(job_id, "failed", reason="r")
    assert (await rows(session_factory, Job))[0].assigned_printer_id == printer_id


async def test_the_details_writer_writes_as_its_own_plugin_and_only_while_enabled(env, session_factory):
    job_id, _ = await seed(session_factory, env.tmp)
    writer = JobDetailsWriter("prn_b")
    await writer.set(job_id, {"prn_b.note": {"a": 1}})
    row = [r for r in await rows(session_factory, JobDetail) if r.field == "prn_b.note"][0]
    assert (row.origin, row.value) == ("prn_b", {"a": 1})
    with pytest.raises(JobReportError, match="does not exist"):
        await writer.set(999999, {"x": 1})
    await plugin_host.update_config("prn_b", enabled=False)
    with pytest.raises(JobReportError, match="not enabled"):
        await writer.set(job_id, {"x": 1})


async def test_the_plugin_context_carries_a_reporter_a_reader_and_a_writer_bound_to_the_plugin(session_factory):
    register(manifest("ctx_prn"))
    ctx = plugin_host.context_for("ctx_prn")
    assert ctx.job_reporter.plugin_id == "ctx_prn" and ctx.job_details_writer.plugin_id == "ctx_prn"
    assert hasattr(ctx.job_details, "get")


def test_a_plugins_notice_is_prefixed_with_its_id_and_core_events_read_as_before():
    from app.services.queue_engine import _notification_content as content
    plain = content("job.complete", 7, "cube.3mf", "P1S", None)
    assert plain == ("Themis: job complete", "cube.3mf finished printing on P1S.")
    assert content("job.complete", 7, "cube.3mf", "P1S", None, origin="core") == plain
    assert content("job.complete", 7, "cube.3mf", "P1S", None, origin="bambu") == ("Themis: job complete", "bambu: cube.3mf finished printing on P1S.")
    assert content("job.failed", 7, "cube.3mf", "P1S", "runout", origin="bambu")[1] == "bambu: cube.3mf failed on P1S: runout"


async def test_a_print_reported_through_the_plugin_context_can_be_read_back_by_another_plugin(env, session_factory):
    """The whole path a plugin sees: start and finish through `ctx.job_reporter`, then any plugin asks `ctx.job_details` for the details."""
    job_id, printer_id = await seed(session_factory, env.tmp, status="uploading")
    printer_ctx, reader_ctx = plugin_host.context_for("prn_a"), plugin_host.context_for("prn_b")

    assert await printer_ctx.job_reporter.report(job_id, "started") is True
    assert await printer_ctx.job_reporter.report(job_id, "complete", {"filament_usage": {"grams": 7.5, "breakdown": None}}) is True

    got = await reader_ctx.job_details.get(job_id, ["print_start_time", "print_end_time", "result", "filament_usage", "no_such"])
    assert {k: v["status"] for k, v in got.items()} == {"print_start_time": "ok", "print_end_time": "ok", "result": "ok",
                                                         "filament_usage": "ok", "no_such": "unknown_field"}
    assert got["result"]["value"] == "success" and got["filament_usage"]["value"]["grams"] == 7.5 and got["result"]["origin"] == "prn_a"
    names = [r.name for r in await rows(session_factory, EventOutbox)]
    assert sorted(names) == ["job.complete", "job.started"]


async def test_a_report_naming_another_printer_of_the_same_plugin_is_refused(env, session_factory):
    """A printer that came back from the dead must not complete or fail the print the job now has on a different printer."""
    job_id, printer_a = await seed(session_factory, env.tmp, plugin="prn_a")
    async with session_factory() as s:
        other = Printer(name="B", printer_type="bambu", connection_config={}, orca_printer_profiles=[], plugin_id="prn_a")
        s.add(other)
        await s.commit()
        printer_b = other.id
    with pytest.raises(JobReportError, match=f"is on printer {printer_a}, not printer {printer_b}"):
        await JobReporter("prn_a").report(job_id, "complete", printer_id=printer_b)
    assert (await rows(session_factory, Job))[0].status == "printing" and await rows(session_factory, EventOutbox) == []
    with pytest.raises(JobReportError, match="serves 2 printers: pass printer_id"):             # and omitting it is not a way around the check
        await JobReporter("prn_a").report(job_id, "complete")
    assert await JobReporter("prn_a").report(job_id, "complete", printer_id=printer_a) is True


async def test_a_dead_printers_late_completion_cannot_touch_the_job_that_now_prints_elsewhere(env, session_factory):
    job_id, printer_a = await seed(session_factory, env.tmp)
    assert await env.engine.requeue_dead_print(job_id, printer_a, "offline") is True       # A was declared dead; the job is back in the queue
    async with session_factory() as s:
        other = Printer(name="B", printer_type="bambu", connection_config={}, orca_printer_profiles=[], plugin_id="prn_a")
        s.add(other)
        await s.flush()
        job = await s.get(Job, job_id)
        job.status, job.assigned_printer_id = "printing", other.id                          # B claimed and started it
        await s.commit()
    outbox_before = len(await rows(session_factory, EventOutbox))

    await env.engine.handle_print_complete(printer_a)                                       # A comes back and reports its own print ending

    assert (await rows(session_factory, Job))[0].status == "printing"                       # B's print is untouched
    assert len(await rows(session_factory, EventOutbox)) == outbox_before


async def test_usage_reports_are_attempt_bound_ordered_and_preserved_on_completion(env, session_factory):
    job_id, printer_id = await seed(session_factory, env.tmp, status="sliced")
    reporter = JobReporter("prn_a")
    assert await reporter.report(job_id, "started")
    from app.services import job_details
    async with session_factory() as session:
        saved = (await job_details.get_details_in(session, job_id, ["filament_usage_context"], origin="core"))["filament_usage_context"]["value"]
    report = {"version": 1, "attempt_id": saved["attempt_id"], "report_id": "summary", "revision": 2,
              "basis": "physical_slot", "source": "reported", "final": True, "complete": True,
              "grams": 0.0, "breakdown": []}
    assert await reporter.report_usage(job_id, report, printer_id=printer_id)
    assert not await reporter.report_usage(job_id, report, printer_id=printer_id)
    assert not await reporter.report_usage(job_id, {**report, "revision": 1}, printer_id=printer_id)
    with pytest.raises(JobReportError, match="another or unknown print attempt"):
        await reporter.report_usage(job_id, {**report, "attempt_id": "old-print"}, printer_id=printer_id)
    assert await reporter.report(job_id, "complete")
    value = (await job_details.get_details(job_id, ["filament_usage"]))["filament_usage"]["value"]
    assert value["revision"] == 2 and value["source"] == "reported" and value["grams"] == 0.0
    with pytest.raises(JobReportError, match="no longer printing"):                 # late, not a duplicate: the plugin is told
        await reporter.report_usage(job_id, {**report, "revision": 3}, printer_id=printer_id)


@pytest.mark.parametrize("tool_mapped,basis", [(True, "physical_slot"), (False, "slicer_filament")])
async def test_the_usage_basis_comes_from_the_printer_plugin_even_with_no_connected_client(
        env, session_factory, monkeypatch, tool_mapped, basis):
    """Review: a start reported while no control client is connected must still read usage the way that printer slices."""
    from app.services import job_details, printer_client_factory
    job_id, _ = await seed(session_factory, env.tmp, status="sliced")

    class Kind:
        slice_tool_mapping = tool_mapped
    monkeypatch.setattr(printer_client_factory, "client_class", lambda key: Kind if key == "prn_a" else None)
    with pytest.raises(KeyError):
        env.mgr.get_client(1)                                                       # nothing connected
    assert await JobReporter("prn_a").report(job_id, "started")
    async with session_factory() as session:
        ctx = (await job_details.get_details_in(session, job_id, ["filament_usage_context"], origin="core"))["filament_usage_context"]["value"]
    assert ctx["basis"] == basis


async def test_a_details_filament_usage_on_complete_never_replaces_a_versioned_report(env, session_factory):
    """Review: an unversioned details payload would turn a partial report back into a full estimate deduction."""
    from app.services import job_details
    job_id, printer_id = await seed(session_factory, env.tmp, status="sliced")
    reporter = JobReporter("prn_a")
    assert await reporter.report(job_id, "started")
    async with session_factory() as session:
        ctx = (await job_details.get_details_in(session, job_id, ["filament_usage_context"], origin="core"))["filament_usage_context"]["value"]
    partial = {"version": 1, "attempt_id": ctx["attempt_id"], "report_id": "live", "revision": 1, "basis": "physical_slot",
               "source": "reported", "final": False, "complete": False, "grams": 3.0, "breakdown": [{"index": 0, "grams": 3.0}]}
    assert await reporter.report_usage(job_id, partial, printer_id=printer_id)
    assert await reporter.report(job_id, "complete", details={"filament_usage": {"grams": 99.0, "breakdown": None}, "print_seconds": 42})
    details = await job_details.get_details(job_id, ["filament_usage", "print_seconds"])
    assert details["filament_usage"]["value"]["version"] == 1 and details["filament_usage"]["value"]["complete"] is False
    assert details["filament_usage"]["value"]["grams"] == 3.0
    assert details["print_seconds"]["value"] == 42                                  # the rest of the payload still lands
