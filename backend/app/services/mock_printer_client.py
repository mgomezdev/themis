"""Mock printer driver for E2E and integration testing.

Always reports as connected; accepts file uploads and print commands without
touching any real network. Exposes printer_type="mock" so test suites can
register a printer that the queue will immediately treat as eligible.

Usage — register via the Themis API:
    POST /api/v1/printers
    {"name": "Mock Printer", "printer_type": "mock", "connection_config": {}}
"""
from __future__ import annotations

from .abstract_printer_client import AbstractPrinterClient, PrinterCapabilities, StartPrintOptions


class MockPrinterClient(AbstractPrinterClient):
    printer_type = "mock"

    def __init__(self, **_ignored):
        self._printing = False

    # --- Connection lifecycle ---

    @property
    def connected(self) -> bool:
        return True

    def connect(self, loop=None) -> None:
        pass

    def disconnect(self, timeout: int = 0) -> None:
        self._printing = False

    # --- State ---

    @property
    def is_idle(self) -> bool:
        return not self._printing

    @property
    def is_printing(self) -> bool:
        return self._printing

    # --- Print control ---

    def start_print(self, file_name: str, options: StartPrintOptions | None = None) -> bool:
        self._printing = True
        return True

    def stop_print(self) -> bool:
        self._printing = False
        return True

    def pause_print(self) -> bool:
        return True

    def resume_print(self) -> bool:
        return True

    # --- Command interface ---

    def send_gcode(self, gcode: str) -> bool:
        return True

    def request_status_update(self) -> None:
        pass

    # --- Capabilities ---

    def get_capabilities(self) -> PrinterCapabilities:
        return PrinterCapabilities(
            file_upload=True,
            pause_resume=True,
            gcode=True,
        )

    @property
    def file_upload_supported(self) -> bool:
        return True

    def upload_file(self, data: bytes, filename: str) -> bool:
        return True
