"""_do_upload_and_print: the last hop before a physical print. Upload/start failures must end the job as
`failed` with a specific reason and leave the printer un-gated; success must hand the printer the right
file + options and raise the ready-for-work gate. Real QueueEngine/PrinterManager/DB, fake vendor client."""
import os
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.models import GcodeFile, Job, Printer, UploadedFile
from app.services.printer_manager import PrinterManager
from app.services.queue_engine import QueueEngine


class _Broadcasts:
    def __init__(self) -> None:
        self.job_updates: list[dict] = []

    async def __call__(self, event, payload):
        if event == "job_update":
            self.job_updates.append(payload)


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


def _client(*, upload_supported: bool = True, upload=True, start=True) -> MagicMock:
    """`upload` / `start`: a bool return value, or an Exception instance to raise."""
    c = MagicMock()
    c.file_upload_supported = upload_supported
    for method, outcome in ((c.upload_file, upload), (c.start_print, start)):
        if isinstance(outcome, Exception):
            method.side_effect = outcome
        else:
            method.return_value = outcome
    return c


async def _seed_uploading_job(factory, tmp_path, status: str = "uploading"):
    gcode = tmp_path / "job.gcode"
    gcode.write_bytes(b"G28\n")
    async with factory() as s:
        s.add(Printer(id=1, name="P1", printer_type="bambu", connection_config={}, awaiting_plate_clear=False))
        f = UploadedFile(original_filename="a.3mf", stored_path="/x/a.3mf", plates=[], uploaded_at="t")
        s.add(f)
        await s.flush()
        j = Job(uploaded_file_id=f.id, plate_number=2, queue_position=1.0, status=status,
                assigned_printer_id=1, created_at="t", updated_at="t")
        s.add(j)
        await s.flush()
        s.add(GcodeFile(job_id=j.id, printer_id=1, path=str(gcode)))
        await s.commit()
        return j.id, gcode


async def _state(factory, job_id):
    async with factory() as s:
        job = await s.get(Job, job_id)
        printer = await s.get(Printer, 1)
        gcode_rows = list((await s.execute(select(GcodeFile).where(GcodeFile.job_id == job_id))).scalars())
        return job, printer, gcode_rows


@pytest.mark.parametrize("client_kwargs, expected_reason, start_called", [
    pytest.param({"upload": False}, "Gcode upload reported failure by printer", False, id="upload-returns-false"),
    pytest.param({"upload": OSError("disk full")}, "Gcode upload failed: disk full", False, id="upload-raises"),
    pytest.param({"start": False}, "Start print reported failure by printer", True, id="start-returns-false"),
    pytest.param({"start": RuntimeError("busy")}, "Start print failed: busy", True, id="start-raises"),
])
async def test_upload_or_start_failure_fails_the_job_and_leaves_the_printer_ungated(
    engine, mgr, session_factory, tmp_path, broadcasts, client_kwargs, expected_reason, start_called,
):
    job_id, gcode = await _seed_uploading_job(session_factory, tmp_path)
    client = _client(**client_kwargs)
    mgr._clients[1] = client

    await engine._do_upload_and_print(job_id, 1, str(gcode), 2, None)

    job, printer, gcode_rows = await _state(session_factory, job_id)
    assert job.status == "failed"
    assert job.block_reason == expected_reason
    assert job.assigned_printer_id is None
    assert job.completed_at is not None
    assert gcode_rows == [] and not os.path.exists(gcode)
    assert printer.awaiting_plate_clear is False
    assert mgr.is_awaiting_plate_clear(1) is False
    assert client.start_print.called is start_called
    assert broadcasts.job_updates[-1]["status"] == "failed"


async def test_successful_start_uploads_the_file_starts_with_plate_and_tray_and_raises_the_gate(
    engine, mgr, session_factory, tmp_path, broadcasts,
):
    job_id, gcode = await _seed_uploading_job(session_factory, tmp_path)
    client = _client()
    mgr._clients[1] = client

    await engine._do_upload_and_print(job_id, 1, str(gcode), 2, 3)

    client.upload_file.assert_called_once_with(b"G28\n", "job.gcode")
    (filename, opts), _ = client.start_print.call_args
    assert filename == "job.gcode"
    assert opts.plate_id == 2
    assert opts.gcode_path == "job.gcode"
    assert opts.ams_mapping == [3]
    job, printer, gcode_rows = await _state(session_factory, job_id)
    assert job.status == "printing"
    assert job.printed_on_printer_id == 1  # survives later failure/cancel, unlike assigned_printer_id
    assert printer.awaiting_plate_clear is True
    assert mgr.is_awaiting_plate_clear(1) is True
    assert len(gcode_rows) == 1 and gcode.exists()  # kept until the print ends
    assert broadcasts.job_updates[-1]["status"] == "printing"


async def test_printers_without_upload_support_skip_upload_and_pass_no_ams_mapping(engine, mgr, session_factory, tmp_path):
    job_id, gcode = await _seed_uploading_job(session_factory, tmp_path)
    client = _client(upload_supported=False)
    mgr._clients[1] = client

    await engine._do_upload_and_print(job_id, 1, str(gcode), 2, None)

    client.upload_file.assert_not_called()
    (_, opts), _ = client.start_print.call_args
    assert opts.ams_mapping is None
    assert (await _state(session_factory, job_id))[0].status == "printing"


@pytest.mark.parametrize("final_status", ["cancelled", "complete"])
async def test_job_resolved_while_starting_is_not_flipped_back_to_printing(engine, mgr, session_factory, tmp_path, final_status):
    job_id, gcode = await _seed_uploading_job(session_factory, tmp_path, status=final_status)
    mgr._clients[1] = _client()

    await engine._do_upload_and_print(job_id, 1, str(gcode), 2, None)

    job, printer, _ = await _state(session_factory, job_id)
    assert job.status == final_status
    assert printer.awaiting_plate_clear is False
    assert mgr.is_awaiting_plate_clear(1) is False
