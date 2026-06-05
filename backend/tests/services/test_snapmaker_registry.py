from app.services.printer_client_factory import (
    get_printer_types_for_ui, create_client_from_config,
)
from app.services.snapmaker_client import SnapmakerExtendedClient


def test_snapmaker_in_printer_types():
    types = {t["printer_type"]: t for t in get_printer_types_for_ui()}
    assert "snapmaker_extended" in types
    assert types["snapmaker_extended"]["display_name"] == "Snapmaker U1 (Extended)"
    names = [f["name"] for f in types["snapmaker_extended"]["connection_fields"]]
    assert names == ["ip_address", "port", "api_key"]


def test_create_snapmaker_client():
    c = create_client_from_config("snapmaker_extended",
                                  {"ip_address": "192.168.0.119", "port": 7125})
    assert isinstance(c, SnapmakerExtendedClient)
    assert c.control_endpoint() == ("192.168.0.119", 7125)
