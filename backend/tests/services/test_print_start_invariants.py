"""Bounded dispatch checks exercised by the pstack queue and plate skills."""

import asyncio
from threading import Event
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy import select

from app.models import GcodeFile, Job, JobPrinterConfig, Printer
from app.services import completion_events
from app.services.abstract_printer_client import PrinterCapabilities
from app.services.printer_manager import PrinterManager, printer_manager
from app.services.queue_engine import QueueEngine
from tests.services.test_queue_upload_start import _seed_uploading_job


class SimulatedExit(BaseException):
    """Discard uncommitted state without invoking application error handling."""


async def _engine(factory, printer, data_dir):
    manager = PrinterManager()
    manager.set_session_factory(factory)
    await manager.load_awaiting_plate_clear_from_db()
    await manager.load_configured_from_db()
    manager.register_client(1, printer)
    return QueueEngine(factory, manager, Mock(_data_dir=data_dir))


def _printer(**kwargs):
    printer = Mock(connected=True, is_idle=True, **kwargs)
    printer.serialize_state.side_effect = lambda _: {"state": "IDLE" if printer.is_idle else "RUNNING"}
    printer.get_capabilities.return_value = PrinterCapabilities()
    printer.start_print.return_value = True
    return printer


async def _queue_cycle(engine):
    await engine._process_queue()
    await asyncio.gather(*engine._job_tasks)


@pytest.mark.parametrize("queue_on", [False, True])
@pytest.mark.parametrize("plate_clear", [False, True])
async def test_parked_slice_start_guards(session_factory, tmp_path, queue_on, plate_clear):
    job_id, path = await _seed_uploading_job(session_factory, tmp_path, status="sliced")
    async with session_factory() as session:
        row = await session.get(Printer, 1)
        row.configured = True
        row.queue_on = queue_on
        row.awaiting_plate_clear = not plate_clear
        await session.commit()
    printer = _printer(file_upload_supported=True)
    engine = await _engine(session_factory, printer, tmp_path)
    try:
        await _queue_cycle(engine)
        assert printer.upload_file.call_count == int(queue_on and plate_clear)
        assert printer.start_print.call_count == int(queue_on and plate_clear)
        async with session_factory() as session:
            assert (await session.get(Job, job_id)).status == ("printing" if queue_on and plate_clear else "sliced")
            assert path.is_file()
    finally:
        await engine.stop()


@pytest.mark.parametrize("change", ["none", "queue-off", "plate-occupied", "both"])
async def test_flags_at_end_of_upload(client, session_factory, tmp_path, change):
    job_id, path = await _seed_uploading_job(session_factory, tmp_path)
    async with session_factory() as session:
        (await session.get(Printer, 1)).configured = True
        await session.commit()
    printer = _printer(file_upload_supported=True)
    loop = asyncio.get_running_loop()
    uploading, release = asyncio.Event(), Event()

    def upload(*_):
        loop.call_soon_threadsafe(uploading.set)
        return release.wait(10)

    printer.upload_file.side_effect = upload
    engine = await _engine(session_factory, printer, tmp_path)
    dispatch = asyncio.create_task(engine._do_upload_and_print(job_id, 1, str(path), 2, None))
    try:
        await asyncio.wait_for(uploading.wait(), 10)
        if change in ("queue-off", "both"):
            response = await client.patch("/api/v1/printers/1", json={"queue_on": False})
            assert response.status_code == 200, response.text
        async with session_factory() as session:
            row = await session.get(Printer, 1)
            if change in ("plate-occupied", "both"):
                row.awaiting_plate_clear = True
            await session.commit()
        release.set()
        await dispatch
        assert printer.start_print.call_count == int(change == "none")
        async with session_factory() as session:
            assert (await session.get(Job, job_id)).status == ("printing" if change == "none" else "sliced")
            assert await session.scalar(select(GcodeFile.id).where(GcodeFile.job_id == job_id)) and path.is_file()
        if change == "queue-off":
            response = await client.patch("/api/v1/printers/1", json={"queue_on": True})
            assert response.status_code == 200, response.text
            await _queue_cycle(engine)
            printer.start_print.assert_called_once()
            async with session_factory() as session:
                assert (await session.get(Job, job_id)).status == "printing"
    finally:
        release.set()
        await asyncio.gather(dispatch, return_exceptions=True)
        await engine.stop()


@pytest.mark.parametrize(
    ("cut", "finish"),
    [("during-upload", None), ("start-unconfirmed", None)] + [
        (cut, finish)
        for cut in ("after-acceptance", "before-start-commit", "after-start-commit")
        for finish in ("before-restart", "after-restart")
    ],
)
async def test_restart_preserves_plate_gate(session_factory, tmp_path, cut, finish):
    first_id, first_file = await _seed_uploading_job(session_factory, tmp_path)
    next_file = tmp_path / "next.gcode"
    next_file.write_bytes(b"G28\n")
    async with session_factory() as session:
        (await session.get(Printer, 1)).configured = True
        first = await session.get(Job, first_id)
        following = Job(uploaded_file_id=first.uploaded_file_id, plate_number=2, queue_position=2.0,
                        status="sliced", created_at="t", updated_at="t")
        session.add(following)
        await session.flush()
        session.add_all([JobPrinterConfig(job_id=following.id, printer_id=1, print_profile=""),
                         GcodeFile(job_id=following.id, printer_id=1, path=str(next_file))])
        await session.commit()

    printer = _printer(file_upload_supported=cut == "during-upload")
    accepted = []

    def start(filename, _options):
        if filename == first_file.name and cut == "start-unconfirmed":
            raise SimulatedExit
        accepted.append(filename)
        printer.is_idle = False
        return True

    printer.start_print.side_effect = start
    original = await _engine(session_factory, printer, tmp_path)
    try:
        with pytest.MonkeyPatch.context() as fault:
            if cut == "during-upload":
                printer.upload_file.side_effect = SimulatedExit
            elif cut == "after-acceptance":
                fault.setattr(original, "_origin_of", AsyncMock(side_effect=SimulatedExit))
            elif cut == "before-start-commit":
                fault.setattr(completion_events, "enqueue_job_started", AsyncMock(side_effect=SimulatedExit))
            elif cut == "after-start-commit":
                fault.setattr(original, "_broadcast_job", AsyncMock(side_effect=SimulatedExit))
            with pytest.raises(SimulatedExit):
                await original._do_upload_and_print(first_id, 1, str(first_file), 2, None)
    finally:
        await original.stop()

    initial = [first_file.name] if finish else []
    assert accepted == initial
    printer.file_upload_supported = False
    if finish == "before-restart":
        printer.is_idle = True

    restarted = await _engine(session_factory, printer, tmp_path)
    try:
        with patch.object(restarted, "_loop", new_callable=AsyncMock):
            await restarted.start()
        if finish == "after-restart":
            await _queue_cycle(restarted)
            assert accepted == initial
            printer.is_idle = True
        await _queue_cycle(restarted)
        expected = [next_file.name] if cut == "during-upload" else initial
        assert accepted == expected, "The queue started a second print without plate clearance"
    finally:
        await restarted.stop()


@pytest.mark.parametrize("entry", ["stored", "upload"])
@pytest.mark.parametrize("clear", [False, True])
async def test_direct_starts_require_clearance_even_when_queue_is_off(client, create_printer, session_factory, entry, clear):
    printer_id = await create_printer()
    async with session_factory() as session:
        row = await session.get(Printer, printer_id)
        row.queue_on = False
        row.awaiting_plate_clear = not clear
        await session.commit()
    printer = _printer(file_upload_supported=True)
    printer.get_capabilities.return_value = PrinterCapabilities(direct_upload=True, file_browser=True)
    printer_manager.register_client(printer_id, printer)
    base = f"/api/v1/printers/{printer_id}"
    if entry == "stored":
        response = await client.post(f"{base}/files/print", json={"file_id": "job.gcode"})
    else:
        response = await client.post(f"{base}/upload", data={"start": "true"}, files={"file": ("job.gcode", b"G28\n")})
    assert response.status_code == (200 if clear else 409), response.text
    assert printer.start_print.call_count == int(clear)


async def test_queue_parks_without_uploading_after_concurrent_direct_start(client, session_factory, tmp_path):
    job_id, _ = await _seed_uploading_job(session_factory, tmp_path, status="sliced")
    async with session_factory() as session:
        (await session.get(Printer, 1)).configured = True
        (await session.get(Job, job_id)).assigned_printer_id = None
        await session.commit()
    printer = _printer(file_upload_supported=True)
    printer.get_capabilities.return_value = PrinterCapabilities(direct_upload=True)
    printer_manager.register_client(1, printer)
    engine = await _engine(session_factory, printer, tmp_path)
    loop = asyncio.get_running_loop()
    uploading, release = asyncio.Event(), Event()

    def upload(_data, name):
        if name != "direct.gcode":
            return False
        loop.call_soon_threadsafe(uploading.set)
        return release.wait(10)

    printer.upload_file.side_effect = upload
    request = asyncio.create_task(client.post("/api/v1/printers/1/upload", data={"start": "true"},
                                              files={"file": ("direct.gcode", b"G28\n")}))
    try:
        await asyncio.wait_for(uploading.wait(), 10)
        await engine._process_queue()
        async with session_factory() as session:
            assert (await session.get(Job, job_id)).status == "uploading"
        release.set()
        assert (await request).status_code == 200
        await asyncio.gather(*engine._job_tasks)
        printer.upload_file.assert_called_once_with(b"G28\n", "direct.gcode")
        assert [call.args[0] for call in printer.start_print.call_args_list] == ["direct.gcode"]
        async with session_factory() as session:
            assert (await session.get(Job, job_id)).status == "sliced"
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)
        await engine.stop()


@pytest.mark.parametrize("cancel_request", [False, True])
async def test_clear_is_rejected_while_start_command_is_in_flight(client, create_printer, session_factory, cancel_request):
    printer_id = await create_printer()
    printer = _printer(file_upload_supported=False)
    printer.get_capabilities.return_value = PrinterCapabilities(file_browser=True)
    printer_manager.register_client(printer_id, printer)
    loop = asyncio.get_running_loop()
    started, release = asyncio.Event(), Event()

    def start(*_):
        loop.call_soon_threadsafe(started.set)
        return release.wait(10)

    printer.start_print.side_effect = start
    base = f"/api/v1/printers/{printer_id}"
    request = asyncio.create_task(client.post(f"{base}/files/print", json={"file_id": "job.gcode"}))
    try:
        await asyncio.wait_for(started.wait(), 10)
        assert (await client.post(f"{base}/plate-cleared")).status_code == 409
        if cancel_request:
            request.cancel()
            assert (await client.post(f"{base}/plate-cleared")).status_code == 409
            request.cancel()
            assert (await client.post(f"{base}/plate-cleared")).status_code == 409
        async with session_factory() as session:
            assert (await session.get(Printer, printer_id)).awaiting_plate_clear is True
        release.set()
        if cancel_request:
            with pytest.raises(asyncio.CancelledError):
                await request
        else:
            assert (await request).status_code == 200
        printer.is_idle = False
        assert (await client.post(f"{base}/plate-cleared")).status_code == 409
        printer.is_idle = True
        assert (await client.post(f"{base}/plate-cleared")).status_code == 200
        async with session_factory() as session:
            assert (await session.get(Printer, printer_id)).awaiting_plate_clear is False
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)
