"""Shared topology/dispatch synchronization and neutral validation."""
import asyncio
from weakref import WeakValueDictionary
from sqlalchemy import select
from ..models import Printer, JobPrinterConfig, Job

_locks: WeakValueDictionary[tuple[asyncio.AbstractEventLoop, int], asyncio.Lock] = WeakValueDictionary()
_refresh_generations: dict[int, int] = {}


def lock_for(printer_id: int) -> asyncio.Lock:
    return _locks.setdefault((asyncio.get_running_loop(), printer_id), asyncio.Lock())


async def check_device(session, printer_id: int):
    printer = await session.get(Printer, printer_id)
    if printer is None:
        raise ValueError('Printer was removed')
    await session.refresh(printer)
    from .printer_manager import printer_manager
    if printer_id in printer_manager._topology_invalid:
        raise ValueError('Printer topology refresh is pending or failed')
    if printer.topology is None:
        raise ValueError('Printer topology has not been configured; refresh before printing')
    from ..plugins.printer_topology import PrinterTopology
    PrinterTopology.model_validate(printer.topology)
    from .printer_identity import dormant_reason
    if dormant_reason(printer.plugin_id):
        raise ValueError('Printer plugin is inactive')
    if printer.topology_error:
        raise ValueError('Printer topology refresh failed; refresh before printing')
    return printer


async def check_assignment(session, job_id: int, printer_id: int):
    printer = await check_device(session, printer_id)
    config = (await session.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id, JobPrinterConfig.printer_id == printer_id))).scalar_one_or_none()
    if config is None:
        raise ValueError('Printer assignment configuration was removed')
    if printer.topology_revision and config.topology_revision != printer.topology_revision:
        raise ValueError('Printer topology changed after this assignment was prepared')
    if config is not None:
        from .filament_matching import still_loaded, plan_for
        problem = still_loaded(plan_for(config, printer.loaded_filaments or []), printer.loaded_filaments or [])
        if problem:
            raise ValueError(problem)
    job = await session.get(Job, job_id)
    if job is None or job.status != 'uploading' or job.assigned_printer_id != printer_id:
        raise ValueError('Print assignment was cancelled or changed')
    return printer



class TopologyRefreshError(ValueError):
    pass


async def refresh_device(session, printer_id, configuration=None):
    from .printer_manager import printer_manager
    from .queue_engine import queue_engine
    from ..plugins.printer_topology import PrinterTopology
    generation = _refresh_generations.get(printer_id, 0) + 1
    _refresh_generations[printer_id] = generation
    printer_manager._topology_invalid.add(printer_id)
    async with lock_for(printer_id):
        printer = await session.get(Printer, printer_id)
        if printer is None:
            raise ValueError('Printer not found')
        try:
            client = printer_manager.get_client(printer_id)
            if not client.connected:
                raise ValueError('Printer not connected')
        except (KeyError, ValueError) as exc:
            raise TopologyRefreshError('Printer session is unavailable for refresh') from exc
        await session.refresh(printer)
        supplied = configuration or {}
        configuration = {**(printer.topology_config or {}), **supplied}
        if 'positions' not in supplied:
            known = list((printer.topology or {}).get('positions', []))
            loaded = printer.loaded_filaments or []
            by_id = {}
            for index, slot in enumerate(loaded):
                fallback = known[index]['id'] if index < len(known) else str(slot.get('slot', index))
                key = str(slot.get('position_id') or fallback)
                # Position bookkeeping is not part of the material description.
                filament = {k: v for k, v in slot.items() if v is not None and k not in {'slot', 'position_id', 'empty', 'reported'}}
                by_id[key] = None if slot.get('empty') else filament
            ids = [p['id'] for p in known]
            ids.extend(key for key in by_id if key not in ids)
            if ids:
                configuration['positions'] = [{'id': key, 'filament': by_id.get(key)} for key in ids]
        try:
            raw = await asyncio.to_thread(client.refresh_topology, configuration)
            topology = PrinterTopology.model_validate(raw.model_dump() if isinstance(raw, PrinterTopology) else raw)
            if printer_manager._clients.get(printer_id) is not client:
                raise ValueError('Printer session changed during refresh')
            from .printer_identity import dormant_reason
            if dormant_reason(printer.plugin_id):
                raise ValueError('Printer plugin became inactive during refresh')
        except Exception as exc:
            printer.topology_error = str(exc) or type(exc).__name__
            printer_manager._topology_invalid.add(printer_id)
            await session.commit()
            if printer_manager._on_state_broadcast:
                await printer_manager._on_state_broadcast('printer_topology', {'printer_id': printer_id, 'error': 'Refresh failed', 'revision': printer.topology_revision})
            raise TopologyRefreshError('Topology refresh failed; last snapshot retained') from exc
        await session.refresh(printer)
        snapshot = topology.model_dump(mode='json')
        if snapshot != printer.topology:
            printer.topology_revision += 1
        printer.topology, printer.topology_config, printer.topology_error = snapshot, configuration, None
        # Existing scheduling/inventory use a position-aligned list. Keep empty placeholders.
        from .inventory.refs import preserve_slot_keys
        previous = {str(s.get('position_id', s.get('slot'))): s for s in (printer.loaded_filaments or [])}
        slots = []
        for index, position in enumerate(topology.positions):
            slot = {**(position.filament or {}), 'slot': index, 'position_id': position.id, 'empty': position.filament is None, 'reported': position.reported}
            if position.id in previous:
                slot = preserve_slot_keys(previous[position.id], slot)
            slots.append(slot)
        printer.loaded_filaments = slots
        await session.commit()
        if _refresh_generations[printer_id] == generation:
            printer_manager._topology_invalid.discard(printer_id)
        printer_manager._topology_validated.add(printer_id)
        queue_engine.wake()
        if printer_manager._on_state_broadcast:
            await printer_manager._on_state_broadcast('printer_topology', {'printer_id': printer_id, 'topology': snapshot, 'revision': printer.topology_revision})
        return {'topology': snapshot, 'revision': printer.topology_revision}
