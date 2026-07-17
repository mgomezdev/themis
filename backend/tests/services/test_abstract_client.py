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


# ---------------------------------------------------------------------------
# New: fan_control / temp_control capability flags + default no-op methods
# ---------------------------------------------------------------------------

class _Dummy(AbstractPrinterClient):
    printer_type = "dummy"

    @property
    def connected(self): return False

    def connect(self, loop=None): pass

    def disconnect(self, timeout=0): pass

    def start_print(self, f, opts=None): return False

    def stop_print(self): return False

    def pause_print(self): return False

    def resume_print(self): return False

    def send_gcode(self, g): return False

    def request_status_update(self): pass


def test_capabilities_has_fan_control_flag():
    assert PrinterCapabilities().fan_control is False


def test_capabilities_has_temp_control_flag():
    assert PrinterCapabilities().temp_control is False


def test_set_fan_speeds_default_returns_false():
    assert _Dummy().set_fan_speeds(100, 100, 100) is False


def test_set_bed_temp_default_returns_false():
    assert _Dummy().set_bed_temp(60) is False


def test_orca_export_args_default_is_raw_gcode():
    # Default printer (Klipper/Centauri) reports no extra args -> raw gcode output.
    assert _Dummy().orca_export_args("mymodel_p1_j7") == []


def test_bambu_orca_export_args_names_3mf_after_job():
    from app.services.bambu_mqtt import BambuMQTTClient
    client = BambuMQTTClient.__new__(BambuMQTTClient)  # method is pure; skip __init__
    assert client.orca_export_args("mymodel_p1_j7") == ["--export-3mf", "mymodel_p1_j7.gcode.3mf"]
