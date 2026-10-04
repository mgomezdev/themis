"""The core operator workflow across every layer — HTTP routes, queue engine, printer manager and a printer
client — with only the slicer faked: queue two jobs, the first prints, the printer is gated until the operator
clears the plate, then the second job is claimed. API tests patch the engine and engine tests seed the DB
directly, so this is the only place the seams between the layers are exercised together."""
from tests.fake_providers import FakeSlicingProvider
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest_asyncio

from app.services.printer_manager import printer_manager
from app.services.queue_engine import QueueEngine

GCODE = "; filament used [g] = 12.5\n; estimated printing time (normal mode) = 1h 5m 30s\nG28\n"


@pytest_asyncio.fixture
async def farm(client, session_factory, tmp_path, monkeypatch):
    """A real engine wired to the routes and to the shared printer_manager, as the app lifespan does."""
    def slice_to_per_job_file(req):  # the real slicer writes gcode/<job_id>/...; never share a file between jobs
        out = tmp_path / f"job-{req.job_id}.gcode"
        out.write_text(GCODE)
        return str(out)

    slicer = MagicMock()
    slicer.slice.side_effect = slice_to_per_job_file
    broadcasts: list[tuple[str, object]] = []

    async def broadcast(event_name, payload):
        broadcasts.append((event_name, payload))

    engine = QueueEngine(session_factory, printer_manager, slicer, broadcast_cb=broadcast)

    async def run_slice_inline(item):
        _, _seq, coro = item
        await coro

    engine._slice_queue.put = run_slice_inline  # type: ignore[method-assign]
    monkeypatch.setattr("app.api.routes.jobs.queue_engine", engine)
    monkeypatch.setattr("app.api.routes.printers.queue_engine", engine)

    saved = (printer_manager._session_factory, printer_manager._on_job_complete, printer_manager._on_state_broadcast)
    printer_manager.set_session_factory(session_factory)
    printer_manager.set_job_complete_callback(engine.handle_print_complete)
    printer_manager.set_broadcast_callback(broadcast)

    with patch("app.services.queue_engine.get_slicing_provider", return_value=FakeSlicingProvider()):
        yield SimpleNamespace(engine=engine, slicer=slicer, broadcasts=broadcasts)

    printer_manager._session_factory, printer_manager._on_job_complete, printer_manager._on_state_broadcast = saved
    engine._executor.shutdown(wait=False)


async def run_cycle(engine: QueueEngine) -> None:
    await engine._process_queue()
    tasks = [t for t in asyncio.all_tasks() if t.get_name().startswith(("slice-", "upload-"))]
    if tasks:
        await asyncio.gather(*tasks)


async def _queue(client) -> dict[int, str]:
    return {j["id"]: j["status"] for j in (await client.get("/api/v1/queue")).json()}


async def test_two_jobs_flow_from_queue_to_print_to_plate_clear_to_next_print(client, farm, upload_3mf):
    # ── set up: a mock printer, one uploaded plate, two queued jobs ──────────────────────────────
    created = await client.post("/api/v1/printers", json={
        "name": "Mock", "printer_type": "mock", "connection_config": {},
        "orca_printer_profiles": ["Mock Machine"], "current_orca_printer_profile": "Mock Machine",
    })
    assert created.status_code == 201, created.text
    printer_id = created.json()["id"]
    mock_printer = printer_manager.get_client(printer_id)
    file_id = await upload_3mf()
    job_ids = []
    for _ in range(2):
        resp = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm",
                                 "filament_type": "any", "filament_color": "any"}],
        })
        assert resp.status_code == 201, resp.text
        job_ids.append(resp.json()["id"])
    first, second = job_ids
    assert await _queue(client) == {first: "queued", second: "queued"}

    # ── cycle 1: the head job is sliced, uploaded and started; the printer is now gated ──────────
    await run_cycle(farm.engine)

    assert await _queue(client) == {first: "printing", second: "queued"}
    job = (await client.get(f"/api/v1/jobs/{first}")).json()
    assert job["assigned_printer_id"] == printer_id
    assert job["actual_filament_grams"] == 12.5 and job["actual_seconds"] == 3930
    (req,) = [c.args[0] for c in farm.slicer.slice.call_args_list]
    assert (req.job_id, req.plate_number, req.machine_preset, req.process_preset) == (first, 1, "Mock Machine", "0.20mm")
    assert mock_printer.is_printing is True
    printer = (await client.get(f"/api/v1/printers/{printer_id}")).json()
    assert printer["awaiting_plate_clear"] is True
    assert printer_manager.is_awaiting_plate_clear(printer_id) is True
    assert ("job_update", first) in [(e, p["id"]) for e, p in farm.broadcasts if e == "job_update"]

    # ── cycle 2 while it prints: the busy printer can't take it, so the next job is pre-sliced and parked ──
    await run_cycle(farm.engine)
    assert await _queue(client) == {first: "printing", second: "sliced"}
    assert (await client.get(f"/api/v1/jobs/{second}")).json()["assigned_printer_id"] is None
    assert [c.args[0].job_id for c in farm.slicer.slice.call_args_list] == [first, second]
    assert mock_printer.is_printing is True

    # ── the print ends: printer goes idle and its client signals completion ─────────────────────
    mock_printer.stop_print()
    await printer_manager.on_print_complete(printer_id, None)

    done = (await client.get(f"/api/v1/jobs/{first}")).json()
    assert done["status"] == "complete" and done["completed_at"] is not None
    assert await _queue(client) == {second: "sliced"}  # finished jobs leave the queue

    # ── cycle 3: the printer is idle again but the plate hasn't been cleared → the parked job waits ──
    assert mock_printer.is_idle is True
    await run_cycle(farm.engine)
    assert await _queue(client) == {second: "sliced"}
    assert mock_printer.is_printing is False

    # ── operator clears the plate → the next cycle starts the parked job WITHOUT slicing it again ───
    assert (await client.post(f"/api/v1/printers/{printer_id}/plate-cleared")).status_code == 200
    assert farm.engine._event.is_set(), "plate-cleared must wake the queue loop"
    await run_cycle(farm.engine)

    assert await _queue(client) == {second: "printing"}
    assert (await client.get(f"/api/v1/jobs/{second}")).json()["assigned_printer_id"] == printer_id
    assert farm.slicer.slice.call_count == 2
    assert mock_printer.is_printing is True
    assert (await client.get(f"/api/v1/printers/{printer_id}")).json()["awaiting_plate_clear"] is True
