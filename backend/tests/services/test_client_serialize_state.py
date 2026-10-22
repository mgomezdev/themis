"""`AbstractPrinterClient.serialize_state` is the vendor's status dict; the printer manager overlays the shared keys."""
from app.services.printer_manager import printer_manager
from tests.services.fake_printer_plugin import FakeClient


def test_base_default_shape():
    c = FakeClient("1.2.3.4")
    assert c.serialize_state(7) == {"id": 7, "printer_type": "fake_vendor", "connected": False}
    c.connect()
    assert c.serialize_state(7)["connected"] is True


class _Custom(FakeClient):
    def serialize_state(self, printer_id):
        return {"id": printer_id, "printer_type": "fake_vendor", "bed_temp": 61.5,
                "connected": "stale", "capabilities": "stale", "awaiting_plate_clear": "stale"}


def test_manager_returns_the_clients_dict_with_shared_keys_overlaid():
    c = _Custom("1.2.3.4")
    c.connect()
    printer_manager.register_client(42, c)
    printer_manager._awaiting_plate_clear.add(42)

    state = printer_manager.get_normalized_state(42)

    assert state["bed_temp"] == 61.5 and state["id"] == 42
    assert state["connected"] is True
    assert state["awaiting_plate_clear"] is True
    assert isinstance(state["capabilities"], dict) and "ams" in state["capabilities"]


def test_manager_uses_the_default_for_a_client_without_an_override():
    printer_manager.register_client(43, FakeClient("1.2.3.4"))
    state = printer_manager.get_normalized_state(43)
    assert state["id"] == 43 and state["printer_type"] == "fake_vendor"
    assert state["connected"] is False and state["awaiting_plate_clear"] is False
