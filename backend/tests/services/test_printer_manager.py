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
