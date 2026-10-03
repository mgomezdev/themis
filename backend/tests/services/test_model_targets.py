"""Make/model job targets (BIZ-187): materialization into per-printer configs, and the queue engine picking them up."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.models import Job, JobModelTarget, JobPrinterConfig, Printer, UploadedFile
from app.services import model_targets
from app.services.queue_engine import QueueEngine
from tests.services.test_queue_engine import _make_mock_printer_manager
from tests.waiting import settle_background_tasks

P1S = "Bambu Lab P1S 0.4 nozzle"
X1C = "Bambu Lab X1 Carbon 0.4 nozzle"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _printer(factory, pid: int, profile: str | None) -> None:
    async with factory() as s:
        s.add(Printer(id=pid, name=f"P{pid}", printer_type="bambu", connection_config={},
                      current_orca_printer_profile=profile))
        await s.commit()


async def _targeted_job(factory, profile: str = P1S, status: str = "queued") -> tuple[int, int]:
    """A job with a model target and no explicit configs -> (job_id, target_id)."""
    async with factory() as s:
        f = UploadedFile(original_filename="t.3mf", plates=[], uploaded_at=_now())
        s.add(f)
        await s.flush()
        j = Job(uploaded_file_id=f.id, plate_number=1, queue_position=1.0, status=status,
                created_at=_now(), updated_at=_now())
        s.add(j)
        await s.flush()
        t = JobModelTarget(job_id=j.id, machine_profile=profile, print_profile="0.20mm",
                           filament_type="PLA", filament_color="any")
        s.add(t)
        await s.commit()
        return j.id, t.id


async def _configs(factory, job_id: int) -> list[JobPrinterConfig]:
    async with factory() as s:
        return list((await s.execute(
            select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id).order_by(JobPrinterConfig.printer_id)
        )).scalars().all())


@pytest.mark.asyncio
async def test_materialize_job_creates_configs_only_for_matching_printers(session_factory):
    await _printer(session_factory, 1, P1S)
    await _printer(session_factory, 2, X1C)
    await _printer(session_factory, 3, P1S)
    job_id, target_id = await _targeted_job(session_factory)

    async with session_factory() as s:
        await model_targets.materialize_job(s, job_id)
        await s.commit()

    cfgs = await _configs(session_factory, job_id)
    assert [c.printer_id for c in cfgs] == [1, 3]
    assert all(c.model_target_id == target_id and c.print_profile == "0.20mm" and c.filament_type == "PLA"
               for c in cfgs)


@pytest.mark.asyncio
async def test_sync_picks_up_printer_added_after_job_creation(session_factory):
    job_id, _ = await _targeted_job(session_factory)
    assert await _configs(session_factory, job_id) == []
    await _printer(session_factory, 7, P1S)

    async with session_factory() as s:
        await model_targets.sync_targets_for_printer(s, await s.get(Printer, 7))
        await s.commit()

    assert [c.printer_id for c in await _configs(session_factory, job_id)] == [7]


@pytest.mark.asyncio
async def test_sync_is_idempotent_and_ignores_other_models(session_factory):
    await _printer(session_factory, 1, P1S)
    await _printer(session_factory, 2, X1C)
    job_id, _ = await _targeted_job(session_factory)
    for _ in range(2):
        async with session_factory() as s:
            for pid in (1, 2):
                await model_targets.sync_targets_for_printer(s, await s.get(Printer, pid))
            await s.commit()
    assert [c.printer_id for c in await _configs(session_factory, job_id)] == [1]


@pytest.mark.asyncio
async def test_sync_removes_config_when_printer_changes_model(session_factory):
    await _printer(session_factory, 1, P1S)
    job_id, _ = await _targeted_job(session_factory)
    async with session_factory() as s:
        await model_targets.materialize_job(s, job_id)
        await s.commit()
    async with session_factory() as s:
        p = await s.get(Printer, 1)
        p.current_orca_printer_profile = X1C
        await model_targets.sync_targets_for_printer(s, p)
        await s.commit()
    assert await _configs(session_factory, job_id) == []


@pytest.mark.asyncio
async def test_sync_keeps_config_of_a_job_already_slicing(session_factory):
    await _printer(session_factory, 1, P1S)
    job_id, _ = await _targeted_job(session_factory)
    async with session_factory() as s:
        await model_targets.materialize_job(s, job_id)
        (await s.get(Job, job_id)).status = "slicing"
        await s.commit()
    async with session_factory() as s:
        p = await s.get(Printer, 1)
        p.current_orca_printer_profile = X1C
        await model_targets.sync_targets_for_printer(s, p)
        await s.commit()
    assert [c.printer_id for c in await _configs(session_factory, job_id)] == [1]


@pytest.mark.asyncio
async def test_explicit_config_wins_over_target_for_same_printer(session_factory):
    await _printer(session_factory, 1, P1S)
    job_id, _ = await _targeted_job(session_factory)
    async with session_factory() as s:
        s.add(JobPrinterConfig(job_id=job_id, printer_id=1, print_profile="0.28mm", filament_profile="PETG"))
        await s.commit()
    async with session_factory() as s:
        await model_targets.materialize_job(s, job_id)
        await model_targets.sync_targets_for_printer(s, await s.get(Printer, 1))
        await s.commit()
    cfgs = await _configs(session_factory, job_id)
    assert len(cfgs) == 1 and cfgs[0].print_profile == "0.28mm" and cfgs[0].model_target_id is None


@pytest.mark.asyncio
async def test_printer_without_profile_matches_nothing(session_factory):
    await _printer(session_factory, 1, None)
    job_id, _ = await _targeted_job(session_factory)
    async with session_factory() as s:
        await model_targets.sync_targets_for_printer(s, await s.get(Printer, 1))
        await s.commit()
    assert await _configs(session_factory, job_id) == []


@pytest.mark.asyncio
async def test_engine_claims_targeted_job_on_matching_printer_only(session_factory):
    await _printer(session_factory, 1, X1C)      # ready but the wrong model
    job_id, _ = await _targeted_job(session_factory)
    qe = QueueEngine(session_factory, _make_mock_printer_manager([1]), MagicMock())

    await qe._process_queue()
    await settle_background_tasks()
    async with session_factory() as s:
        assert (await s.get(Job, job_id)).status == "queued"
    assert await _configs(session_factory, job_id) == []

    await _printer(session_factory, 2, P1S)      # a P1S shows up later and is ready
    qe = QueueEngine(session_factory, _make_mock_printer_manager([1, 2]), MagicMock())
    await qe._process_queue()
    await settle_background_tasks()

    async with session_factory() as s:
        job = await s.get(Job, job_id)
        # Claimed (or blocked on slicer availability) by the P1S - never left queued, never given to the X1C.
        assert job.status != "queued"
    assert [c.printer_id for c in await _configs(session_factory, job_id)] == [2]


@pytest.mark.asyncio
async def test_engine_blocks_targeted_job_on_filament_mismatch(session_factory):
    await _printer(session_factory, 1, P1S)
    async with session_factory() as s:
        (await s.get(Printer, 1)).loaded_filaments = [{"slot": 0, "type": "PETG", "color": "#FFFFFF"}]
        await s.commit()
    job_id, _ = await _targeted_job(session_factory)  # asks for PLA
    qe = QueueEngine(session_factory, _make_mock_printer_manager([1]), MagicMock())

    await qe._process_queue()
    await settle_background_tasks()

    async with session_factory() as s:
        job = await s.get(Job, job_id)
        assert job.status == "blocked" and "filament" in (job.block_reason or "").lower()


@pytest.mark.asyncio
async def test_sync_removes_a_config_whose_target_was_deleted(session_factory):
    """A re-edit that replaced the job's targets must not leave the old materialized row behind."""
    from sqlalchemy import delete
    await _printer(session_factory, 1, P1S)
    job_id, target_id = await _targeted_job(session_factory)
    async with session_factory() as s:
        await model_targets.materialize_job(s, job_id)
        await s.commit()
    async with session_factory() as s:
        await s.execute(delete(JobModelTarget).where(JobModelTarget.id == target_id))
        await s.commit()
    async with session_factory() as s:
        await model_targets.sync_targets_for_printer(s, await s.get(Printer, 1))
        await s.commit()
    assert await _configs(session_factory, job_id) == []


@pytest.mark.asyncio
async def test_a_duplicate_job_printer_config_is_rejected_by_the_database(session_factory):
    from sqlalchemy.exc import IntegrityError
    await _printer(session_factory, 1, P1S)
    job_id, _ = await _targeted_job(session_factory)
    async with session_factory() as s:
        s.add(JobPrinterConfig(job_id=job_id, printer_id=1, print_profile="a"))
        await s.commit()
    with pytest.raises(IntegrityError):
        async with session_factory() as s:
            s.add(JobPrinterConfig(job_id=job_id, printer_id=1, print_profile="b"))
            await s.commit()
