"""BIZ-369: PrinterManager keeps unconfigured printers out of the ready set, flips a printer to configured on its first live
connection, and never builds a client for a printer missing a required connection field."""
import logging
from unittest.mock import MagicMock, patch

from app.models import Printer
from app.services.printer_manager import PrinterManager
from tests.services.test_printer_manager import _make_mock_client

_BAMBU = {"plugin_id": "bambu", "manufacturer_id": "bambu", "model_id": "p1s", "printer_type": "bambu"}


async def _seed(session_factory, **cols) -> int:
    async with session_factory() as s:
        p = Printer(name="P", connection_config=cols.pop("connection_config", {"ip_address": "10.0.0.2"}), **{**_BAMBU, **cols})
        s.add(p)
        await s.commit()
        return p.id


def test_unconfigured_printer_is_never_ready():
    mgr = PrinterManager()
    mgr._clients[1] = _make_mock_client(is_idle=True)
    mgr.set_configured(1, False)
    assert mgr.is_configured(1) is False
    assert mgr.is_printer_ready(1) is False
    mgr.set_configured(1, True)
    assert mgr.is_printer_ready(1) is True


async def test_load_configured_from_db(session_factory):
    a = await _seed(session_factory, configured=True)
    b = await _seed(session_factory, configured=False)
    mgr = PrinterManager()
    mgr.set_session_factory(session_factory)
    mgr.set_configured(999, False)                                    # stale entry must not survive the reload
    await mgr.load_configured_from_db()
    assert mgr._unconfigured == {b}
    assert mgr.is_configured(a) is True


async def test_first_online_state_marks_configured(session_factory):
    pid = await _seed(session_factory, configured=False, config_error="Couldn't reach 10.0.0.2:8883")
    mgr = PrinterManager()
    mgr.set_session_factory(session_factory)
    calls = []

    async def broadcast(event, payload):
        calls.append((event, payload))

    mgr.set_broadcast_callback(broadcast)
    mgr.set_configured(pid, False)
    client = _make_mock_client()
    client.connected = False
    mgr.register_client(pid, client)
    client.connected = True
    await mgr.on_state_change(pid, client.state)

    async with session_factory() as s:
        row = await s.get(Printer, pid)
        assert (row.configured, row.config_error) == (True, None)
    assert mgr.is_configured(pid) is True
    assert ("printer_configured", {"printer_id": pid, "configured": True, "config_error": None}) in calls


async def test_state_while_disconnected_does_not_configure(session_factory):
    pid = await _seed(session_factory, configured=False)
    mgr = PrinterManager()
    mgr.set_session_factory(session_factory)
    mgr.set_configured(pid, False)
    client = _make_mock_client()
    client.connected = False
    mgr.register_client(pid, client)
    await mgr.on_state_change(pid, client.state)
    async with session_factory() as s:
        assert (await s.get(Printer, pid)).configured is False
    assert mgr.is_configured(pid) is False


async def test_startup_skips_printer_missing_required_field(session_factory, caplog):
    skipped = await _seed(session_factory, configured=True, connection_config={"ip_address": "10.0.0.3"})
    full = await _seed(session_factory, configured=True,
                       connection_config={"ip_address": "10.0.0.4", "serial_number": "S", "access_code": "A"})
    mgr = PrinterManager()
    mgr.set_loop(MagicMock())
    with caplog.at_level(logging.WARNING, logger="app.services.printer_manager"), \
         patch("app.services.printer_manager.create_client", return_value=MagicMock()) as built, \
         patch.object(mgr, "connect_printer") as connect:
        await mgr.connect_all_enabled_printers(session_factory)
    assert [c.args[0].id for c in built.call_args_list] == [full]
    assert [c.args[0] for c in connect.call_args_list] == [full]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "Missing Serial Number, Access Code" in warnings[0].getMessage()
    assert not any(r.exc_info for r in caplog.records)
    async with session_factory() as s:
        row = await s.get(Printer, skipped)
        assert row.config_error == "Missing Serial Number, Access Code"
        assert row.configured is True                                   # the reason is recorded; status is left as it was
