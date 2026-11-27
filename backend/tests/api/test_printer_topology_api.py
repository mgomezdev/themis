from unittest.mock import MagicMock
import pytest
from app.models import Printer
from app.services.printer_manager import printer_manager


@pytest.fixture
async def new_printer(create_printer, session_factory):
    async def create():
        pid = await create_printer()
        async with session_factory() as s:
            row = await s.get(Printer, pid)
            row.topology = None
            await s.commit()
        printer_manager._topology_validated.discard(pid)
        return pid
    return create


def snapshot(nozzle=.4):
    return {'tools': [{'id': 'T0', 'nozzle_mm': nozzle}], 'positions': [{'id': 'S0', 'filament': {'type': 'PLA', 'subtype': 'Silk'}}], 'swapping': False}


async def test_refresh_keeps_session_and_atomically_updates_device(client, new_printer, session_factory):
    pid = await new_printer()
    live = MagicMock(connected=True)
    live.refresh_topology.return_value = snapshot()
    printer_manager.register_client(pid, live)
    r = await client.post(f'/api/v1/printers/{pid}/topology/refresh', json={'configuration': {'nozzle_sizes_mm': [.4]}})
    assert r.status_code == 200, r.text
    assert r.json()['topology']['tools'][0]['nozzle_mm'] == .4
    assert printer_manager.get_client(pid) is live
    live.connect.assert_not_called(); live.disconnect.assert_not_called()
    async with session_factory() as s:
        row = await s.get(Printer, pid)
        assert row.topology['positions'][0]['id'] == 'S0'
        assert row.topology_config == {'nozzle_sizes_mm': [.4]}
    live.refresh_topology.return_value = snapshot(.6)
    r2 = await client.post(f'/api/v1/printers/{pid}/topology/refresh', json={})
    assert r2.json()['topology']['tools'][0]['nozzle_mm'] == .6
    assert r2.json()['revision'] == r.json()['revision'] + 1
    assert live.refresh_topology.call_args.args[0]['nozzle_sizes_mm'] == [.4]


@pytest.mark.parametrize('result', ['invalid', 'error'])
async def test_failed_refresh_retains_snapshot_but_blocks_dispatch(client, new_printer, session_factory, result):
    pid = await new_printer()
    live = MagicMock(connected=True)
    live.refresh_topology.return_value = snapshot()
    printer_manager.register_client(pid, live)
    assert (await client.post(f'/api/v1/printers/{pid}/topology/refresh', json={})).status_code == 200
    if result == 'error':
        live.refresh_topology.side_effect = RuntimeError('probe failed')
    else:
        live.refresh_topology.return_value = {'tools': [], 'positions': []}
    assert (await client.post(f'/api/v1/printers/{pid}/topology/refresh', json={})).status_code == 502
    async with session_factory() as s:
        row = await s.get(Printer, pid)
        assert row.topology['tools'][0]['nozzle_mm'] == .4 and row.topology_error
    assert not printer_manager.is_printer_ready(pid)
    live.disconnect.assert_not_called()


async def test_disabled_plugin_cannot_refresh_or_pause(client, new_printer):
    from app.plugins.host import plugin_host
    await plugin_host.start()
    pid = await new_printer()
    live = MagicMock(connected=True)
    printer_manager.register_client(pid, live)
    await plugin_host.update_config('bambu', enabled=False)
    assert (await client.post(f'/api/v1/printers/{pid}/topology/refresh', json={})).status_code == 409
    assert (await client.post(f'/api/v1/printers/{pid}/pause')).status_code == 409
    live.pause_print.assert_not_called(); live.refresh_topology.assert_not_called()


async def test_plugin_pushed_change_uses_same_snapshot_publication(client, new_printer, session_factory):
    pid = await new_printer()
    live = MagicMock(connected=True)
    live.refresh_topology.return_value = snapshot()
    printer_manager.register_client(pid, live)
    printer_manager.set_session_factory(session_factory)
    await printer_manager.on_topology_change(pid)
    async with session_factory() as s:
        row = await s.get(Printer, pid)
        assert row.topology['tools'][0]['nozzle_mm'] == .4
    live.refresh_topology.return_value = snapshot(.8)
    await printer_manager.on_topology_change(pid)
    async with session_factory() as s:
        row = await s.get(Printer, pid)
        assert row.topology['tools'][0]['nozzle_mm'] == .8
        assert row.topology_revision == 2
    live.disconnect.assert_not_called()


async def test_concurrent_refreshes_do_not_lose_revisions(client, new_printer, mocker):
    import asyncio
    import threading
    pid = await new_printer()
    from app.api.routes import printer_topology as route
    original_get = route._get_or_404
    prefetched = asyncio.Event()
    reads = 0
    async def get_row(*args):
        nonlocal reads
        row = await original_get(*args)
        reads += 1
        if reads == 2:
            prefetched.set()
        return row
    mocker.patch.object(route, '_get_or_404', get_row)
    entered = threading.Event()
    release = threading.Event()
    live = MagicMock(connected=True)
    def first(_):
        entered.set()
        assert release.wait(5)
        return snapshot(.4)
    live.refresh_topology.side_effect = [snapshot(.4), snapshot(.6)]
    # Force both requests to read the row before the first commits.
    live.refresh_topology.side_effect = lambda cfg: first(cfg) if not entered.is_set() else snapshot(.6)
    printer_manager.register_client(pid, live)
    a = asyncio.create_task(client.post(f'/api/v1/printers/{pid}/topology/refresh', json={}))
    assert await asyncio.to_thread(entered.wait, 5)
    b = asyncio.create_task(client.post(f'/api/v1/printers/{pid}/topology/refresh', json={}))
    await asyncio.wait_for(prefetched.wait(), 5)
    release.set()
    ra, rb = await asyncio.gather(a, b)
    assert ra.status_code == rb.status_code == 200
    assert rb.json()['revision'] == ra.json()['revision'] + 1


async def test_runtime_manual_filament_edit_refreshes_topology_without_reconnect(client, new_printer, session_factory):
    pid = await new_printer()
    live = MagicMock(connected=True)
    live.refresh_topology.side_effect = lambda cfg: {**snapshot(), 'positions': cfg.get('positions') or snapshot()['positions']}
    printer_manager.register_client(pid, live)
    printer_manager.set_session_factory(session_factory)
    assert (await client.post(f'/api/v1/printers/{pid}/topology/refresh', json={})).status_code == 200
    r = await client.patch(f'/api/v1/printers/{pid}', json={'loaded_filaments': [{'slot': 0, 'position_id': 'S0', 'type': 'PETG'}]})
    assert r.status_code == 200, r.text
    async with session_factory() as s:
        row = await s.get(Printer, pid)
        assert row.topology['positions'][0]['filament']['type'] == 'PETG'
        assert row.topology_error is None
    live.connect.assert_not_called(); live.disconnect.assert_not_called()


async def test_first_successful_connection_acquires_declared_topology(client, new_printer, session_factory):
    from app.plugins.mock.client import MockPrinterClient
    pid = await new_printer()
    live = MockPrinterClient()
    printer_manager.register_client(pid, live)
    printer_manager.set_session_factory(session_factory)
    await printer_manager.mark_configured(pid)
    async with session_factory() as s:
        row = await s.get(Printer, pid)
        assert row.topology['tools'][0]['nozzle_mm'] == .4
    assert printer_manager.get_client(pid) is live and printer_manager.is_printer_ready(pid)


async def test_disabled_plugin_cannot_list_storage_or_stop_via_job_cancel(client, create_printer, create_job, session_factory):
    from app.plugins.host import plugin_host
    from app.models import Job
    await plugin_host.start()
    pid = await create_printer()
    jid = await create_job(printer_id=pid)
    live = MagicMock(connected=True)
    live.list_files.return_value = []
    printer_manager.register_client(pid, live)
    async with session_factory() as s:
        job = await s.get(Job, jid)
        job.status, job.assigned_printer_id = 'printing', pid
        await s.commit()
    await plugin_host.update_config('bambu', enabled=False)
    r = await client.get('/api/v1/printers/files/all')
    assert r.status_code == 200
    assert next(p for p in r.json()['printers'] if p['printer_id'] == pid)['error']
    live.list_files.assert_not_called()
    assert (await client.post(f'/api/v1/jobs/{jid}/cancel')).status_code == 200
    live.stop_print.assert_not_called()


async def test_unchanged_manual_plugin_refresh_keeps_revision(client, new_printer):
    from app.plugins.mock.client import MockPrinterClient
    pid = await new_printer()
    live = MockPrinterClient()
    printer_manager.register_client(pid, live)
    first = await client.post(f'/api/v1/printers/{pid}/topology/refresh', json={})
    for _ in range(4):
        repeated = await client.post(f'/api/v1/printers/{pid}/topology/refresh', json={})
        assert first.status_code == repeated.status_code == 200
        assert repeated.json() == first.json()


@pytest.mark.parametrize('stored', [True, False])
async def test_direct_starts_require_valid_topology(client, new_printer, stored):
    pid = await new_printer()
    live = MagicMock(connected=True, is_idle=True)
    printer_manager.register_client(pid, live)
    if stored:
        r = await client.post(f'/api/v1/printers/{pid}/files/print', json={'file_id':'part.gcode'})
    else:
        r = await client.post(f'/api/v1/printers/{pid}/upload', files={'file':('part.gcode',b'G28')}, data={'start':'true'})
    assert r.status_code == 409, r.text
    live.start_print.assert_not_called()
    live.upload_file.assert_not_called()


async def test_refresh_pending_during_direct_upload_prevents_start(client, create_printer):
    pid = await create_printer()
    live = MagicMock(connected=True, is_idle=True)
    printer_manager.register_client(pid, live)
    def uploading(*_):
        printer_manager._topology_invalid.add(pid)
        return True
    live.upload_file.side_effect = uploading
    r = await client.post(f'/api/v1/printers/{pid}/upload', files={'file':('part.gcode',b'G28')}, data={'start':'true'})
    assert r.status_code == 409, r.text
    live.start_print.assert_not_called()
    assert not printer_manager.is_awaiting_plate_clear(pid)


async def test_finishing_one_refresh_cannot_clear_a_later_pending_refresh(client, new_printer, session_factory, mocker):
    import asyncio
    import threading
    from app.api.routes import printer_topology as route
    from app.services.printer_topology import check_device, lock_for
    pid = await new_printer()
    second_read = asyncio.Event()
    original_get = route._get_or_404
    reads = 0
    async def get_row(*args):
        nonlocal reads
        row = await original_get(*args)
        reads += 1
        if reads == 2:
            second_read.set()
        return row
    mocker.patch.object(route, '_get_or_404', get_row)
    entered, release = threading.Event(), threading.Event()
    live = MagicMock(connected=True)
    def report(_):
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        return snapshot()
    live.refresh_topology.side_effect = report
    printer_manager.register_client(pid, live)
    first = asyncio.create_task(client.post(f'/api/v1/printers/{pid}/topology/refresh', json={}))
    assert await asyncio.to_thread(entered.wait, 10)
    queued = asyncio.Event()
    async def dispatch_guard():
        queued.set()
        async with lock_for(pid):
            async with session_factory() as session:
                try:
                    await check_device(session, pid)
                except ValueError as exc:
                    return str(exc)
                return None
    dispatch = asyncio.create_task(dispatch_guard())
    await queued.wait()  # dispatch is queued behind the first refresh
    second = asyncio.create_task(client.post(f'/api/v1/printers/{pid}/topology/refresh', json={}))
    await asyncio.wait_for(second_read.wait(), 10)
    release.set()
    first_result, problem, second_result = await asyncio.gather(first, dispatch, second)
    assert first_result.status_code == second_result.status_code == 200
    assert problem is not None and 'pending' in problem


async def test_a_refused_filament_edit_leaves_the_printer_dispatchable(client, new_printer, session_factory, monkeypatch):
    """GH #259: the edit marked the topology invalid in memory before its save; a save refused after that (an ownership
    conflict, a nozzle mapping removed meanwhile) rolled the edit back but left the printer undispatchable in memory."""
    from fastapi import HTTPException
    from app.api.routes import printers as printer_routes
    pid = await new_printer()
    live = MagicMock(connected=True)
    live.refresh_topology.side_effect = lambda cfg: snapshot()
    printer_manager.register_client(pid, live)
    printer_manager.set_session_factory(session_factory)
    assert (await client.post(f'/api/v1/printers/{pid}/topology/refresh', json={})).status_code == 200
    assert pid not in printer_manager._topology_invalid

    async def refused(session, **_kw):
        await session.rollback()
        raise HTTPException(409, "This printer is already owned by another binding")

    monkeypatch.setattr(printer_routes, "_save_printer", refused)
    r = await client.patch(f'/api/v1/printers/{pid}', json={'loaded_filaments': [{'slot': 0, 'position_id': 'S0', 'type': 'PETG'}]})
    assert r.status_code == 409, r.text
    assert pid not in printer_manager._topology_invalid
    async with session_factory() as s:
        assert (await s.get(Printer, pid)).topology_error is None
