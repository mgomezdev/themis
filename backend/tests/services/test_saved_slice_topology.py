"""A hand-selected saved slice must match live nozzles, even with the same machine preset."""
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.routes.jobs import PrinterConfigInput, _check_version_printers
from app.models import Job, JobPrinterConfig, Printer, SlicedVersion
from tests.services.test_slice_reuse import _add_version
from tests.services.test_slice_save import env, _engine, _seed  # noqa: F401 -- shared isolated library fixture
from tests.waiting import settle_background_tasks, wait_until


async def prepared(factory, library, saved, current):
    version_id = await _add_version(factory, library)
    await _seed(factory, save=False, printer_type='moonraker')
    async with factory() as session:
        printer = await session.get(Printer, 1)
        printer.topology = {
            'tools': [{'id': f'T{i}', 'nozzle_mm': size} for i, size in enumerate(current)],
            'positions': [{'id': str(i), 'filament': {'type': 'PETG'}} for i in range(len(current))],
            'swapping': len(current) > 1,
        }
        printer.loaded_filaments = [{"slot": i, "type": "PETG", "color": "#000000", "filament_profile": "Generic PETG"} for i in range(len(current))]
        printer.topology_revision = 2
        version = await session.get(SlicedVersion, version_id)
        version.nozzle_sizes_mm = saved
        await session.commit()
    return version_id


@pytest.mark.parametrize('saved,current', [([.4], [.6]), ([.4,.6], [.6,.4]), (None, [.4]), ([.4], [.4,.4])])
async def test_selected_saved_slice_rejects_mismatched_or_unknown_nozzles(session_factory, env, saved, current):
    vid = await prepared(session_factory, env, saved, current)
    async with session_factory() as session:
        version = await session.get(SlicedVersion, vid)
        with pytest.raises(HTTPException, match='nozzle') as exc:
            await _check_version_printers(session, version, [PrinterConfigInput(printer_id=1, print_profile='')], [])
        assert exc.value.status_code == 422


@pytest.mark.parametrize('saved,current,allowed', [([.4], [.6], False), (None, [.4], False), ([.4,.6], [.4,.6], True)])
async def test_queue_revalidates_selected_saved_nozzles_before_start(session_factory, tmp_path, env, saved, current, allowed):
    vid = await prepared(session_factory, env, saved, current)
    engine, manager = _engine(session_factory, tmp_path)
    async with session_factory() as session:
        version = await session.get(SlicedVersion, vid)
        now = datetime.now(timezone.utc).isoformat()
        job = Job(uploaded_file_id=version.file_id, sliced_version_id=version.id, plate_number=1,
                  queue_position=0, status='queued', eligibility_confirmed=True, created_at=now, updated_at=now)
        session.add(job)
        await session.flush()
        session.add(JobPrinterConfig(job_id=job.id, printer_id=1, print_profile='', ask_type='PETG'))
        await session.commit()
        job_id = job.id
    await engine._process_queue()
    async def finished():
        async with session_factory() as session:
            return (await session.get(Job, job_id)).status in ('printing', 'blocked', 'failed')
    await wait_until(finished, what='saved slice compatibility decision')
    await settle_background_tasks()
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        assert job.status == ('printing' if allowed else 'blocked')
        if not allowed:
            assert 'nozzle' in job.block_reason.lower()
    assert manager.get_client.return_value.start_print.call_count == int(allowed)
    engine._slicer.slice.assert_not_called()


async def test_incompatible_saved_nozzles_are_not_listed_as_printable(client, session_factory, env):
    vid = await prepared(session_factory, env, [.4], [.6])
    async with session_factory() as session:
        source_id = (await session.get(SlicedVersion, vid)).source_file_id
    response = await client.get(f'/api/v1/files/{source_id}/sliced-versions')
    assert response.status_code == 200
    assert response.json()[0]['printable_now'] is False
