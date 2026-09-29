"""MockPrinterClient is the stand-in printer for end-to-end and integration tests (printer_type "mock")."""
from app.services.mock_printer_client import MockPrinterClient
from app.services.printer_client_factory import create_client_from_config


def test_is_always_connected_and_idle_until_a_print_starts():
    printer = MockPrinterClient()

    assert printer.connected is True
    assert printer.is_idle is True
    assert printer.is_printing is False


def test_start_print_makes_it_busy_and_stop_or_disconnect_frees_it():
    printer = MockPrinterClient()

    assert printer.start_print("job.gcode") is True
    assert (printer.is_printing, printer.is_idle) == (True, False)
    assert printer.stop_print() is True
    assert (printer.is_printing, printer.is_idle) == (False, True)

    printer.start_print("job.gcode")
    printer.disconnect()
    assert printer.is_idle is True


def test_accepts_uploads_and_commands_and_advertises_its_capabilities():
    printer = MockPrinterClient()

    assert printer.file_upload_supported is True
    assert printer.upload_file(b"G28\n", "job.gcode") is True
    assert printer.send_gcode("M105") is True
    assert printer.pause_print() is True and printer.resume_print() is True
    caps = printer.get_capabilities()
    assert (caps.file_upload, caps.pause_resume, caps.gcode) == (True, True, True)


def test_factory_builds_it_from_any_config_and_ignores_stray_fields():
    printer = create_client_from_config("mock", {"ip_address": "ignored"})

    assert isinstance(printer, MockPrinterClient)
    assert printer.printer_type == "mock"
