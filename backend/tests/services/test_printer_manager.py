import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.printer_manager import PrinterManager
from app.services.abstract_printer_client import PrinterCapabilities


def _make_mock_client(printer_type="bambu", is_idle=True):
    client = MagicMock()
    client.printer_type = printer_type
    client.connected = True
    client.is_idle = is_idle
    client.is_printing = not is_idle
    client.get_capabilities.return_value = PrinterCapabilities(pause_resume=True)
    client.state = MagicMock()
    client.state.state = "IDLE" if is_idle else "RUNNING"
    client.state.progress = 0.0
    client.state.temperatures = {}
    client.state.current_print = None
    client.state.remaining_time = 0
    client.state.layer_num = 0
    client.state.total_layers = 0
    client.state.raw_data = {}
    return client


def test_manager_starts_empty():
    mgr = PrinterManager()
    assert mgr.get_all_printer_ids() == []


def test_register_and_get_client():
    mgr = PrinterManager()
    client = _make_mock_client()
    mgr._clients[1] = client
    assert mgr.get_client(1) is client


def test_get_client_missing_raises():
    mgr = PrinterManager()
    with pytest.raises(KeyError):
        mgr.get_client(999)


def test_awaiting_plate_clear_default_false():
    mgr = PrinterManager()
    mgr._clients[1] = _make_mock_client()
    assert mgr.is_awaiting_plate_clear(1) is False


def test_set_awaiting_plate_clear():
    mgr = PrinterManager()
    mgr._clients[1] = _make_mock_client()
    mgr._awaiting_plate_clear.add(1)
    assert mgr.is_awaiting_plate_clear(1) is True


def test_printer_ready_requires_idle_and_no_plate():
    mgr = PrinterManager()
    client = _make_mock_client(is_idle=True)
    mgr._clients[1] = client
    assert mgr.is_printer_ready(1) is True
    mgr._awaiting_plate_clear.add(1)
    assert mgr.is_printer_ready(1) is False


def test_printer_not_ready_when_printing():
    mgr = PrinterManager()
    client = _make_mock_client(is_idle=False)
    mgr._clients[1] = client
    assert mgr.is_printer_ready(1) is False


def test_get_normalized_state_bambu():
    mgr = PrinterManager()
    client = _make_mock_client(printer_type="bambu")
    mgr._clients[1] = client
    state = mgr.get_normalized_state(1)
    assert state["id"] == 1
    assert state["connected"] is True
    assert "state" in state
    assert "capabilities" in state


def test_get_all_printer_ids():
    mgr = PrinterManager()
    mgr._clients[1] = _make_mock_client()
    mgr._clients[2] = _make_mock_client()
    assert sorted(mgr.get_all_printer_ids()) == [1, 2]


# ---------------------------------------------------------------------------
# The gate survives restarts (DB is the source of truth) and is set when a print completes
# ---------------------------------------------------------------------------

async def _session_factory_with_printers(flags: list[bool]):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.database import Base
    from app.models import Printer

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        for i, flag in enumerate(flags, start=1):
            s.add(Printer(id=i, name=f"P{i}", printer_type="bambu", connection_config={}, awaiting_plate_clear=flag))
        await s.commit()
    return factory, engine


async def test_load_awaiting_plate_clear_from_db_restores_exactly_the_flagged_printers():
    factory, engine = await _session_factory_with_printers([True, False, True])
    mgr = PrinterManager()
    mgr.set_session_factory(factory)
    mgr.set_awaiting_plate_clear(99, True)  # stale entry from before the reload must not survive

    await mgr.load_awaiting_plate_clear_from_db()

    assert mgr._awaiting_plate_clear == {1, 3}
    await engine.dispose()


async def test_load_awaiting_plate_clear_from_db_without_session_factory_is_a_noop():
    mgr = PrinterManager()
    mgr.set_awaiting_plate_clear(7, True)

    await mgr.load_awaiting_plate_clear_from_db()

    assert mgr._awaiting_plate_clear == {7}


async def test_on_print_complete_sets_gate_in_manager_and_db_then_notifies():
    from app.models import Printer

    factory, engine = await _session_factory_with_printers([False])
    mgr = PrinterManager()
    mgr.set_session_factory(factory)
    mgr._clients[1] = _make_mock_client()
    calls: list = []

    async def on_job_complete(printer_id):
        calls.append(("job_complete", printer_id))

    async def broadcast(event, payload):
        calls.append((event, payload))

    mgr.set_job_complete_callback(on_job_complete)
    mgr.set_broadcast_callback(broadcast)

    await mgr.on_print_complete(1, vendor_state=None)

    assert mgr.is_awaiting_plate_clear(1) is True
    async with factory() as s:
        assert (await s.get(Printer, 1)).awaiting_plate_clear is True
    assert [c[0] for c in calls] == ["job_complete", "plate_clear_required", "printer_state"]
    assert calls[0] == ("job_complete", 1)
    assert calls[1] == ("plate_clear_required", {"printer_id": 1})
    assert calls[2][1]["awaiting_plate_clear"] is True
    await engine.dispose()
