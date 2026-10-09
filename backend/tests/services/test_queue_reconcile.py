"""_reconcile_printing_jobs: every queue cycle it catches print endings whose completion event was missed
(MQTT reconnect, restart, network blip). Real QueueEngine + real PrinterManager + in-memory DB; only the
vendor client is faked."""
import os
from unittest.mock import MagicMock

from app.plugins.bambu.client import serialize_bambu

import pytest
from sqlalchemy import select

from app.models import GcodeFile, Job, Printer, UploadedFile
from app.services.abstract_printer_client import PrinterCapabilities
from app.services.printer_manager import PrinterManager
from app.services.queue_engine import QueueEngine

FAILURE_REASON = "print cancelled or ended with failure on the printer"


def _client(state: str = "IDLE", connected: bool = True, idle: bool = True) -> MagicMock:
    c = MagicMock()
    c.printer_type = "bambu"
    c.connected = connected
    c.is_idle = idle
    c.get_capabilities.return_value = PrinterCapabilities()
    c.state = MagicMock()
    c.state.state = state
    c.serialize_state.side_effect = lambda printer_id: serialize_bambu(c.state, printer_id)   # as the Bambu client does
    return c


class _Broadcasts:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def __call__(self, event, payload):
        self.events.append((event, payload))

    def job_updates(self) -> list[dict]:
        return [p for e, p in self.events if e == "job_update"]


@pytest.fixture
def broadcasts() -> _Broadcasts:
    return _Broadcasts()


@pytest.fixture
def mgr() -> PrinterManager:
    return PrinterManager()


@pytest.fixture
def engine(session_factory, mgr, broadcasts):
    eng = QueueEngine(session_factory, mgr, MagicMock(), broadcast_cb=broadcasts)
    yield eng
    eng._executor.shutdown(wait=False)


async def _seed_printing_job(factory, tmp_path, printer_id: int = 1, *, actual_seconds: int = 120,
                             assigned: bool = True, awaiting_plate_clear: bool = True):
    """A printer flagged awaiting-clear (as print start does) running a job with a parked gcode file."""
    gcode = tmp_path / f"job-{printer_id}.gcode"
    gcode.write_text("G28\n")
    async with factory() as s:
        s.add(Printer(id=printer_id, name=f"P{printer_id}", printer_type="bambu", connection_config={},
                      awaiting_plate_clear=awaiting_plate_clear))
        f = UploadedFile(original_filename="a.3mf", stored_path="/x/a.3mf", plates=[], uploaded_at="t")
        s.add(f)
        await s.flush()
        j = Job(uploaded_file_id=f.id, plate_number=1, queue_position=float(printer_id), status="printing",
                assigned_printer_id=printer_id if assigned else None, actual_seconds=actual_seconds,
                created_at="t", updated_at="t")
        s.add(j)
        await s.flush()
        s.add(GcodeFile(job_id=j.id, printer_id=printer_id, path=str(gcode)))
        await s.commit()
        return j.id, gcode


async def _job(factory, job_id) -> Job:
    async with factory() as s:
        return await s.get(Job, job_id)


async def _gcode_rows(factory, job_id) -> list[GcodeFile]:
    async with factory() as s:
        return list((await s.execute(select(GcodeFile).where(GcodeFile.job_id == job_id))).scalars())


async def test_idle_printer_in_failed_state_fails_the_job_and_cleans_up(engine, mgr, session_factory, tmp_path, broadcasts):
    job_id, gcode = await _seed_printing_job(session_factory, tmp_path)
    mgr._clients[1] = _client(state="FAILED")

    await engine._reconcile_printing_jobs()

    job = await _job(session_factory, job_id)
    assert job.status == "failed"
    assert job.block_reason == FAILURE_REASON
    assert job.assigned_printer_id is None
    assert job.completed_at is not None
    assert job.deduction_skipped is True
    assert await _gcode_rows(session_factory, job_id) == []
    assert not os.path.exists(gcode)
    assert {"id": job_id, "status": "failed"}.items() <= broadcasts.job_updates()[-1].items()


async def test_idle_printer_in_normal_state_completes_the_job_and_accrues_wear_counters(engine, mgr, session_factory, tmp_path, broadcasts):
    job_id, gcode = await _seed_printing_job(session_factory, tmp_path, actual_seconds=120)
    mgr._clients[1] = _client(state="IDLE")

    await engine._reconcile_printing_jobs()

    job = await _job(session_factory, job_id)
    assert job.status == "complete"
    assert job.completed_at is not None
    assert job.block_reason is None
    async with session_factory() as s:
        printer = await s.get(Printer, 1)
        assert printer.lifetime_job_count == 1
        assert printer.lifetime_print_seconds == 120
        # Reconcile must not release the ready-for-work gate: only the operator's plate-cleared does.
        assert printer.awaiting_plate_clear is True
    assert await _gcode_rows(session_factory, job_id) == []
    assert not os.path.exists(gcode)
    assert {"id": job_id, "status": "complete"}.items() <= broadcasts.job_updates()[-1].items()


@pytest.mark.parametrize("client_kwargs", [
    pytest.param({"connected": False}, id="offline"),
    pytest.param({"idle": False}, id="still-printing"),
    pytest.param({"idle": False, "state": "PAUSE"}, id="paused"),
    pytest.param(None, id="no-client-registered"),
])
async def test_job_is_left_alone_unless_the_printer_is_connected_and_idle(engine, mgr, session_factory, tmp_path, broadcasts, client_kwargs):
    job_id, gcode = await _seed_printing_job(session_factory, tmp_path)
    if client_kwargs is not None:
        mgr._clients[1] = _client(**{"state": "FAILED", **client_kwargs})

    await engine._reconcile_printing_jobs()

    job = await _job(session_factory, job_id)
    assert job.status == "printing"
    assert job.assigned_printer_id == 1
    assert len(await _gcode_rows(session_factory, job_id)) == 1
    assert gcode.exists()
    assert broadcasts.events == []


async def test_printing_job_without_an_assigned_printer_is_skipped(engine, mgr, session_factory, tmp_path):
    job_id, _ = await _seed_printing_job(session_factory, tmp_path, assigned=False)

    await engine._reconcile_printing_jobs()

    assert (await _job(session_factory, job_id)).status == "printing"


async def test_a_printer_whose_state_cannot_be_read_does_not_stall_the_others(engine, mgr, session_factory, tmp_path):
    broken_job, _ = await _seed_printing_job(session_factory, tmp_path, printer_id=1)
    healthy_job, _ = await _seed_printing_job(session_factory, tmp_path, printer_id=2)
    broken = _client(state="FAILED")
    broken.get_capabilities.side_effect = RuntimeError("client blew up")
    mgr._clients[1] = broken
    mgr._clients[2] = _client(state="FAILED")

    await engine._reconcile_printing_jobs()

    assert (await _job(session_factory, broken_job)).status == "printing"
    assert (await _job(session_factory, healthy_job)).status == "failed"


async def test_nothing_printing_is_a_noop(engine, mgr, session_factory, broadcasts):
    mgr._clients[1] = _client(state="FAILED")

    await engine._reconcile_printing_jobs()

    assert broadcasts.events == []
