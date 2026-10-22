from app.services.printer_client_factory import client_class, create_client_from_config, printer_type_names
from app.plugins.snapmaker.client import SnapmakerExtendedClient


def test_snapmaker_in_printer_types():
    assert printer_type_names()["snapmaker_extended"] == "Snapmaker U1 (Extended)"
    names = [f.name for f in client_class("snapmaker_extended").connection_fields()]
    assert names == ["ip_address", "port", "api_key"]


def test_create_snapmaker_client():
    c = create_client_from_config("snapmaker_extended",
                                  {"ip_address": "192.168.0.119", "port": 7125})
    assert isinstance(c, SnapmakerExtendedClient)
    assert c.control_endpoint() == ("192.168.0.119", 7125)
