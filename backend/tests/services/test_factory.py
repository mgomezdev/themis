import pytest
from unittest.mock import MagicMock
from app.services.printer_client_factory import (
    get_printer_types_for_ui,
    create_client,
    REGISTRY,
)
from app.models import Printer


def _printer(printer_type: str, config: dict) -> Printer:
    p = Printer()
    p.id = 1
    p.name = "Test"
    p.printer_type = printer_type
    p.connection_config = config
    p.orca_printer_profiles = []
    p.current_orca_printer_profile = None
    p.awaiting_plate_clear = False
    p.enabled = True
    return p


def test_registry_has_bambu():
    assert "bambu" in REGISTRY


def test_registry_has_elegoo():
    assert "elegoo_centauri" in REGISTRY


def test_registry_does_not_have_moonraker():
    assert "moonraker" not in REGISTRY


def test_get_printer_types_returns_list():
    types = get_printer_types_for_ui()
    assert isinstance(types, list)
    assert len(types) == 2


def test_get_printer_types_bambu_fields():
    types = {t["printer_type"]: t for t in get_printer_types_for_ui()}
    assert "bambu" in types
    field_names = [f["name"] for f in types["bambu"]["connection_fields"]]
    assert "serial_number" in field_names
    assert "access_code" in field_names


def test_get_printer_types_elegoo_fields():
    types = {t["printer_type"]: t for t in get_printer_types_for_ui()}
    assert "elegoo_centauri" in types
    field_names = [f["name"] for f in types["elegoo_centauri"]["connection_fields"]]
    assert "ip_address" in field_names
    # camera_url is not a connection field — camera URL is derived from IP at port 3031
    assert "camera_url" not in field_names


def test_create_client_bambu():
    from app.services.bambu_mqtt import BambuMQTTClient
    printer = _printer("bambu", {
        "ip_address": "1.2.3.4",
        "serial_number": "ABC",
        "access_code": "secret",
    })
    client = create_client(printer)
    assert isinstance(client, BambuMQTTClient)


def test_create_client_elegoo():
    from app.services.elegoo_centauri_client import ElegooCentauriClient
    printer = _printer("elegoo_centauri", {"ip_address": "1.2.3.5"})
    client = create_client(printer)
    assert isinstance(client, ElegooCentauriClient)


def test_create_client_unknown_type_raises():
    printer = _printer("moonraker", {"port": 7125})
    with pytest.raises(ValueError, match="Unknown printer type"):
        create_client(printer)
