"""Make/model job targets (BIZ-187): materialization into per-printer configs, and the queue engine picking them up."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.models import Job, JobModelTarget, JobPrinterConfig, Printer, PrinterModelRecord, UploadedFile
from app.services import model_targets
from app.services.queue_engine import QueueEngine
from tests.services.test_queue_engine import _make_mock_printer_manager
from tests.waiting import settle_background_tasks

P1S = "model-p1s"      # printer-model UUIDs (BIZ-294: targets are keyed by model, not by machine profile)
X1C = "model-x1c"


async def _models(factory) -> None:
    async with factory() as s:
        for uid in (P1S, X1C):
            if await s.get(PrinterModelRecord, uid) is None:
                s.add(PrinterModelRecord(id=uid, plugin_id="bambu", manufacturer_id="bambu", model_id=uid, display_name=uid))
        await s.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


PLA_SLOT = [{"slot": 0, "type": "PLA", "subtype": "standard", "color": "#FFFFFF"}]


async def _printer(factory, pid: int, model: str | None, loaded=None) -> None:
    await _models(factory)
    async with factory() as s:
        s.add(Printer(id=pid, name=f"P{pid}", printer_type="bambu", connection_config={}, model_uuid=model,
                      loaded_filaments=loaded or []))
        await s.commit()


async def _targeted_job(factory, model: str = P1S, status: str = "queued") -> tuple[int, int]:
    """A job with a model target and no explicit configs -> (job_id, target_id)."""
    await _models(factory)
    async with factory() as s:
        f = UploadedFile(original_filename="t.3mf", plates=[], uploaded_at=_now())
        s.add(f)
        await s.flush()
        j = Job(uploaded_file_id=f.id, plate_number=1, queue_position=1.0, status=status,
                created_at=_now(), updated_at=_now())
        s.add(j)
        await s.flush()
        t = JobModelTarget(job_id=j.id, model_uuid=model, print_profile="0.20mm",
                           ask_type="PLA", ask_subtype="any")
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
    assert all(c.model_target_id == target_id and c.print_profile == "0.20mm" and (c.ask_type, c.ask_subtype) == ("PLA", "any")
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
        p.model_uuid = X1C
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
        p.model_uuid = X1C
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
async def test_printer_without_a_model_matches_nothing(session_factory):
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

    await _printer(session_factory, 2, P1S, PLA_SLOT)      # a P1S with PLA loaded shows up later and is ready
    qe = QueueEngine(session_factory, _make_mock_printer_manager([1, 2]), MagicMock())
    await qe._process_queue()
    await settle_background_tasks()

    async with session_factory() as s:
        job = await s.get(Job, job_id)
        # Claimed (or blocked on slicer availability) by the P1S - never left queued, never given to the X1C.
        assert job.status != "queued"
    assert [c.printer_id for c in await _configs(session_factory, job_id)] == [2]


@pytest.mark.asyncio
async def test_engine_leaves_a_targeted_job_queued_on_filament_mismatch_until_the_right_filament_is_loaded(session_factory):
    await _printer(session_factory, 1, P1S, [{"slot": 0, "type": "PETG", "subtype": "standard", "color": "#FFFFFF"}])
    job_id, _ = await _targeted_job(session_factory)  # asks for PLA
    qe = QueueEngine(session_factory, _make_mock_printer_manager([1]), MagicMock())

    await qe._process_queue()
    await settle_background_tasks()

    async with session_factory() as s:
        job = await s.get(Job, job_id)
        assert (job.status, job.block_reason) == ("queued", None)        # never blocked or failed for filament alone

    async with session_factory() as s:                                    # PLA gets loaded: claimable on the next cycle
        (await s.get(Printer, 1)).loaded_filaments = PLA_SLOT
        await s.commit()
    await qe._process_queue()
    await settle_background_tasks()
    async with session_factory() as s:
        assert (await s.get(Job, job_id)).status != "queued"


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


@pytest.mark.asyncio
async def test_target_dicts_by_job_groups_per_job_and_keys_targetless_jobs(session_factory):
    a, _ = await _targeted_job(session_factory, P1S)
    b, _ = await _targeted_job(session_factory, X1C)
    async with session_factory() as s:
        f = UploadedFile(original_filename="n.3mf", plates=[], uploaded_at=_now())
        s.add(f)
        await s.flush()
        bare = Job(uploaded_file_id=f.id, plate_number=1, queue_position=3.0, status="queued",
                   created_at=_now(), updated_at=_now())
        s.add(bare)
        await s.commit()
        bare_id = bare.id
    async with session_factory() as s:
        got = await model_targets.target_dicts_by_job(s, [a, b, bare_id])
    assert [t["model_uuid"] for t in got[a]] == [P1S]
    assert [t["model_uuid"] for t in got[b]] == [X1C]
    assert got[bare_id] == []
    async with session_factory() as s:
        assert await model_targets.target_dicts_by_job(s, []) == {}


@pytest.mark.asyncio
async def test_queue_route_reads_model_targets_in_one_query_for_all_jobs(client, session_factory):
    from sqlalchemy import event
    for _ in range(3):
        await _targeted_job(session_factory)
    seen: list[str] = []
    engine = session_factory.kw["bind"]

    def count(conn, cursor, statement, *_):
        if "FROM job_model_targets" in statement:
            seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        resp = await client.get("/api/v1/queue")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3 and all(len(j["model_targets"]) == 1 for j in body)
    assert len(seen) == 1
