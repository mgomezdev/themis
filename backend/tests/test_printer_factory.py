import pytest
from app.services.printer_client_factory import create_client_from_config, REGISTRY


def test_create_client_from_config_bambu():
    client = create_client_from_config(
        "bambu",
        {"ip_address": "192.168.1.100", "access_code": "12345678", "serial_number": "ABCD1234"},
    )
    assert client is not None


def test_create_client_from_config_unknown_type():
    with pytest.raises(ValueError, match="Unknown printer type"):
        create_client_from_config("unknown_type", {})


def test_create_client_from_config_ignores_extra_fields():
    client = create_client_from_config(
        "bambu",
        {"ip_address": "192.168.1.100", "access_code": "12345678", "serial_number": "X1", "extra": "ignored"},
    )
    assert client is not None
