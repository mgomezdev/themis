"""The ready-for-work gate. `awaiting_plate_clear` lives in the DB row AND in the PrinterManager set;
a printer is only eligible for the next job once both are cleared via POST /printers/{id}/plate-cleared."""
from unittest.mock import MagicMock

import pytest

from app.models import Printer
from app.services.printer_manager import printer_manager
from app.services.queue_engine import QueueEngine


@pytest.fixture
def queue_engine(monkeypatch, session_factory):
    """The module singleton is an uninitialized shell outside the app lifespan; install a real engine
    (never started) so wake() is the real thing and its event can be observed."""
    engine = QueueEngine(session_factory, printer_manager, MagicMock())
    monkeypatch.setattr("app.api.routes.printers.queue_engine", engine)
    yield engine
    engine._executor.shutdown(wait=False)


def _idle_connected_client() -> MagicMock:
    client = MagicMock()
    client.connected = True
    client.is_idle = True
    return client


async def _flag_printer_as_awaiting_clear(session_factory, printer_id: int) -> None:
    """What the engine does when a print starts: flag both the DB row and the manager set."""
    async with session_factory() as s:
        (await s.get(Printer, printer_id)).awaiting_plate_clear = True
        await s.commit()
    printer_manager.register_client(printer_id, _idle_connected_client())
    printer_manager.set_awaiting_plate_clear(printer_id, True)


async def test_plate_cleared_releases_db_flag_and_manager_gate_and_wakes_queue(client, session_factory, create_printer, queue_engine):
    printer_id = await create_printer()
    await _flag_printer_as_awaiting_clear(session_factory, printer_id)
    assert printer_manager.is_printer_ready(printer_id) is False  # idle + connected, but gated

    resp = await client.post(f"/api/v1/printers/{printer_id}/plate-cleared")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert (await client.get(f"/api/v1/printers/{printer_id}")).json()["awaiting_plate_clear"] is False
    async with session_factory() as s:
        assert (await s.get(Printer, printer_id)).awaiting_plate_clear is False
    assert printer_manager.is_awaiting_plate_clear(printer_id) is False
    assert printer_manager.is_printer_ready(printer_id) is True
    assert queue_engine._event.is_set(), "clearing the plate must wake the queue loop"


async def test_plate_cleared_is_idempotent(client, session_factory, create_printer, queue_engine):
    printer_id = await create_printer()
    printer_manager.register_client(printer_id, _idle_connected_client())

    assert (await client.post(f"/api/v1/printers/{printer_id}/plate-cleared")).status_code == 200
    assert (await client.post(f"/api/v1/printers/{printer_id}/plate-cleared")).status_code == 200

    assert printer_manager.is_printer_ready(printer_id) is True
    assert (await client.get(f"/api/v1/printers/{printer_id}")).json()["awaiting_plate_clear"] is False


async def test_plate_cleared_for_unknown_printer_is_404_and_leaves_other_gates_alone(client, session_factory, create_printer, queue_engine):
    printer_id = await create_printer()
    await _flag_printer_as_awaiting_clear(session_factory, printer_id)

    resp = await client.post("/api/v1/printers/9999/plate-cleared")

    assert resp.status_code == 404
    assert printer_manager.is_awaiting_plate_clear(printer_id) is True
    assert not queue_engine._event.is_set()
