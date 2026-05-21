import pytest
from app.services.abstract_printer_client import (
    AbstractPrinterClient,
    ConnectionField,
    PrinterCapabilities,
    PrinterFile,
    StartPrintOptions,
)


class MinimalClient(AbstractPrinterClient):
    printer_type = "test"

    @property
    def connected(self) -> bool:
        return True

    def connect(self, loop=None) -> None:
        pass

    def disconnect(self, timeout: int = 0) -> None:
        pass

    def check_staleness(self) -> bool:
        return True

    def start_print(self, file_name, options=None) -> bool:
        return True

    def stop_print(self) -> bool:
        return True

    def pause_print(self) -> bool:
        return True

    def resume_print(self) -> bool:
        return True

    def send_gcode(self, gcode: str) -> bool:
        self._last_gcode = gcode
        return True

    def request_status_update(self) -> None:
        pass


def test_capabilities_defaults():
    caps = PrinterCapabilities()
    assert caps.ams is False
    assert caps.camera is False
    assert caps.pause_resume is False


def test_start_print_options_defaults():
    opts = StartPrintOptions()
    assert opts.plate_id == 1
    assert opts.gcode_path is None
    assert opts.use_ams is True


def test_printer_file_fields():
    f = PrinterFile(id="abc", name="model.3mf", size=1024)
    assert f.modified_at is None


def test_connection_field_defaults():
    cf = ConnectionField(name="serial_number", label="Serial Number", field_type="text")
    assert cf.required is True
    assert cf.default is None


def test_default_capabilities_all_false():
    client = MinimalClient()
    caps = client.get_capabilities()
    assert caps.ams is False
    assert caps.camera is False
    assert caps.chamber_light is False


def test_home_sends_g28():
    client = MinimalClient()
    client.home()
    assert client._last_gcode == "G28"


def test_set_chamber_light_returns_false_by_default():
    client = MinimalClient()
    assert client.set_chamber_light(True) is False


def test_is_idle_false_by_default():
    client = MinimalClient()
    assert client.is_idle is False


def test_is_printing_false_by_default():
    client = MinimalClient()
    assert client.is_printing is False


def test_validate_file_id_rejects_path_traversal():
    client = MinimalClient()
    with pytest.raises(ValueError):
        client._validate_file_id("../../etc/passwd")


def test_validate_file_id_rejects_null_byte():
    client = MinimalClient()
    with pytest.raises(ValueError):
        client._validate_file_id("file\x00name")


def test_validate_file_id_rejects_absolute_path():
    client = MinimalClient()
    with pytest.raises(ValueError):
        client._validate_file_id("/absolute/path")


def test_validate_file_id_rejects_windows_drive():
    client = MinimalClient()
    with pytest.raises(ValueError):
        client._validate_file_id("C:/windows/system32")


def test_validate_file_id_accepts_normal_filename():
    client = MinimalClient()
    client._validate_file_id("my_model.3mf")  # should not raise


def test_validate_file_id_rejects_unc_path():
    client = MinimalClient()
    with pytest.raises(ValueError):
        client._validate_file_id("\\\\server\\share\\file")


def test_connection_fields_default_empty():
    assert MinimalClient.connection_fields() == []
