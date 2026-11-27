"""Serialize hardware start with operator clearance and queue permission changes."""

import asyncio
from weakref import WeakValueDictionary

from sqlalchemy import update

from ..models import Printer

_locks: WeakValueDictionary[tuple[asyncio.AbstractEventLoop, int], asyncio.Lock] = WeakValueDictionary()


def lock_for(printer_id: int) -> asyncio.Lock:
    return _locks.setdefault((asyncio.get_running_loop(), printer_id), asyncio.Lock())


async def reserve_plate(session, manager, printer_id: int, *, queued: bool) -> bool:
    """Persist the gate before submission. The caller holds the printer's start lock."""
    if manager.is_awaiting_plate_clear(printer_id):
        return False
    claim = update(Printer).where(Printer.id == printer_id, Printer.awaiting_plate_clear.is_(False))
    if queued:
        claim = claim.where(Printer.queue_on.is_(True))
    result = await session.execute(claim.values(awaiting_plate_clear=True))
    if not result.rowcount:
        return False
    await session.commit()
    manager.set_awaiting_plate_clear(printer_id, True)
    return True


async def send(client, filename, options, *, executor=None) -> bool:
    """Keep the caller's start lock held until the submitted command returns, even on cancellation."""
    pending = asyncio.get_running_loop().run_in_executor(executor, client.start_print, filename, options)
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not pending.cancelled():
            pending.exception()
        raise
