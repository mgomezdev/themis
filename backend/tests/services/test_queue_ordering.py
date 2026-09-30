"""Queue claim rules across several jobs/printers: strict queue_position order, head-of-line blocking,
one claim per job, and what happens when slicing fails. Real QueueEngine + DB; fake printer manager/slicer."""
import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.models import Job, JobPrinterConfig, Printer, UploadedFile
from app.services.printer_manager import PrinterManager
from app.services.queue_engine import QueueEngine
from app.services.slicer_service import SliceError


class World:
    """Which printers are online (and therefore ready) can change between engine cycles."""

    def __init__(self, tmp_path: Path) -> None:
        self.online: set[int] = set()
        gcode = tmp_path / "out.gcode"
        gcode.write_text("G28\n")
        self.slicer = MagicMock()
        self.slicer.slice.return_value = str(gcode)
        self.mgr = MagicMock(spec=PrinterManager)
        self.mgr._clients = {}  # reconcile looks clients up here; none registered = 'printing' jobs are left alone
        self.mgr.get_all_printer_ids.side_effect = lambda: sorted(self.online)
        self.mgr.is_printer_ready.side_effect = lambda pid: pid in self.online
        client = MagicMock()
        client.file_upload_supported = False
        client.start_print.return_value = True
        self.mgr.get_client.return_value = client
        self.client = client


@pytest.fixture
def world(tmp_path) -> World:
    return World(tmp_path)


@pytest.fixture
def engine(session_factory, world):
    qe = QueueEngine(session_factory, world.mgr, world.slicer)

    async def run_inline(item):  # slice queue worker isn't running in unit tests: run the slice in place
        _, _seq, coro = item
        await coro

    qe._slice_queue.put = run_inline  # type: ignore[method-assign]
    yield qe
    qe._executor.shutdown(wait=False)


async def cycle(qe: QueueEngine) -> None:
    """One queue pass, then wait for the slice/print tasks it spawned."""
    await qe._process_queue()
    tasks = [t for t in asyncio.all_tasks() if t.get_name().startswith("slice-")]
    if tasks:
        await asyncio.gather(*tasks)


async def seed(factory, printers: dict[int, dict], jobs: list[dict]) -> list[int]:
    """printers: {id: {"loaded": [...], "machine": "..."}}; jobs (in insertion order): {"position": float,
    "printers": [ids], "type": "any", "color": "any", "failed": {printer_id: "error"}}. Returns job ids."""
    async with factory() as s:
        for pid, spec in printers.items():
            s.add(Printer(id=pid, name=f"P{pid}", printer_type="elegoo_centauri", connection_config={},
                          current_orca_printer_profile=spec.get("machine", f"Machine P{pid}"),
                          loaded_filaments=spec.get("loaded", [])))
        f = UploadedFile(original_filename="a.3mf", stored_path="/x/a.3mf", plates=[], uploaded_at="t")
        s.add(f)
        await s.flush()
        ids = []
        for spec in jobs:
            j = Job(uploaded_file_id=f.id, plate_number=1, queue_position=spec["position"], status="queued",
                    created_at="t", updated_at="t")
            s.add(j)
            await s.flush()
            for pid in spec["printers"]:
                failed = spec.get("failed", {}).get(pid)
                s.add(JobPrinterConfig(
                    job_id=j.id, printer_id=pid, print_profile="0.20mm", filament_profile="PLA",
                    filament_type=spec.get("type", "any"), filament_color=spec.get("color", "any"),
                    slice_failed=failed is not None, slice_error=failed))
            ids.append(j.id)
        await s.commit()
        return ids


async def job_state(factory, job_id: int) -> Job:
    async with factory() as s:
        return await s.get(Job, job_id)


PLA = [{"slot": 0, "type": "PLA", "color": "#FFFFFF"}]
PETG = [{"slot": 0, "type": "PETG", "color": "#FFFFFF"}]


async def test_a_blocked_job_at_the_head_of_the_line_holds_back_the_jobs_behind_it(engine, world, session_factory):
    """Head-of-line: a job that can't run blocks; the engine must NOT skip ahead to one that can."""
    a, b = await seed(session_factory, {1: {"loaded": PLA}}, [
        {"position": 1.0, "printers": [1], "type": "PETG", "color": "#FFFFFF"},  # needs PETG, printer 1 has PLA
        {"position": 2.0, "printers": [1]},                    # would run fine
    ])
    world.online = {1}

    await cycle(engine)
    await cycle(engine)

    job_a, job_b = await job_state(session_factory, a), await job_state(session_factory, b)
    assert job_a.status == "blocked" and "filament" in job_a.block_reason.lower()
    assert job_b.status == "queued" and job_b.assigned_printer_id is None
    world.slicer.slice.assert_not_called()

    async with session_factory() as s:  # operator loads PETG: the head unblocks and goes first
        (await s.get(Printer, 1)).loaded_filaments = PETG
        await s.commit()
    await cycle(engine)

    job_a, job_b = await job_state(session_factory, a), await job_state(session_factory, b)
    assert (job_a.status, job_a.assigned_printer_id) == ("printing", 1)
    assert job_b.status == "queued"


async def test_jobs_are_claimed_in_queue_position_order_not_insertion_order(engine, world, session_factory):
    later, earlier = await seed(session_factory, {1: {}}, [
        {"position": 2.0, "printers": [1]},
        {"position": 1.0, "printers": [1]},
    ])
    world.online = {1}

    await cycle(engine)

    assert (await job_state(session_factory, earlier)).status == "printing"
    assert (await job_state(session_factory, later)).status == "queued"


async def test_two_idle_printers_take_two_jobs_in_order_and_never_the_same_job(engine, world, session_factory):
    first, second = await seed(session_factory, {1: {}, 2: {}}, [
        {"position": 1.0, "printers": [1, 2]},
        {"position": 2.0, "printers": [1, 2]},
    ])
    world.online = {1, 2}

    await cycle(engine)

    job1, job2 = await job_state(session_factory, first), await job_state(session_factory, second)
    assert (job1.status, job1.assigned_printer_id) == ("printing", 1)
    assert (job2.status, job2.assigned_printer_id) == ("printing", 2)
    assert world.slicer.slice.call_count == 2


async def test_a_job_eligible_on_two_idle_printers_is_claimed_exactly_once(engine, world, session_factory):
    (job_id,) = await seed(session_factory, {1: {}, 2: {}}, [{"position": 1.0, "printers": [1, 2]}])
    world.online = {1, 2}

    await cycle(engine)

    job = await job_state(session_factory, job_id)
    assert (job.status, job.assigned_printer_id) == ("printing", 1)
    assert world.slicer.slice.call_count == 1
    assert world.client.start_print.call_count == 1


async def test_slice_failure_on_one_printer_blocks_the_job_and_another_printer_can_rescue_it(engine, world, session_factory):
    def slice_or_fail(req):
        if req.machine_preset == "Machine P1":
            raise SliceError("profile not found")
        return world.slicer.slice.return_value

    (job_id,) = await seed(session_factory, {1: {}, 2: {}}, [{"position": 1.0, "printers": [1, 2]}])
    world.slicer.slice.side_effect = slice_or_fail

    world.online = {1}
    await cycle(engine)
    job = await job_state(session_factory, job_id)
    assert job.status == "blocked" and "profile not found" in job.block_reason
    async with session_factory() as s:
        failed = {c.printer_id: c.slice_failed for c in (await s.execute(select(JobPrinterConfig))).scalars()}
    assert failed == {1: True, 2: False}

    world.online = {2}
    await cycle(engine)
    job = await job_state(session_factory, job_id)
    assert (job.status, job.assigned_printer_id) == ("printing", 2)
    async with session_factory() as s:
        failed = {c.printer_id: c.slice_failed for c in (await s.execute(select(JobPrinterConfig))).scalars()}
    assert failed == {1: True, 2: False}  # printer 1's failure is remembered, printer 2 never failed


async def test_when_slicing_has_failed_on_every_printer_the_job_stays_blocked_and_nothing_retries(engine, world, session_factory):
    """Pins CURRENT behavior: exhausting all printer configs leaves the job `blocked` with the last slice
    error (until the operator unblocks it). It does NOT become `failed` — CLAUDE.md / docs/agent still say
    it does; see the review report (O9)."""
    (job_id,) = await seed(session_factory, {1: {}, 2: {}}, [
        {"position": 1.0, "printers": [1, 2], "failed": {1: "boom on 1", 2: "boom on 2"}},
    ])
    world.online = {1, 2}

    await cycle(engine)
    await cycle(engine)

    job = await job_state(session_factory, job_id)
    assert job.status == "blocked"
    assert job.status != "failed"
    assert job.block_reason in {"boom on 1", "boom on 2"}  # the last printer processed records its error
    assert job.assigned_printer_id is None
    world.slicer.slice.assert_not_called()
