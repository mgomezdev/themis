"""Mock printer driver for E2E and integration testing.

Always reports as connected; accepts file uploads and print commands without
touching any real network. Exposes printer_type="mock" so test suites can
register a printer that the queue will immediately treat as eligible.

Usage — register via the Themis API:
    POST /api/v1/printers
    {"name": "Mock Printer", "printer_type": "mock", "connection_config": {}}
"""
from __future__ import annotations

from .._protocols.gcode_controls import GcodeControls

from ...services.abstract_printer_client import AbstractPrinterClient, PrinterCapabilities, StartPrintOptions


class MockPrinterClient(GcodeControls, AbstractPrinterClient):
    print_artifact_kind = "gcode"
    raw_gcode_supported = True
    printer_type = "mock"

    def __init__(self, **_ignored):
        self._printing = False

    def refresh_topology(self, configuration: dict):
        from ..printer_topology import PrinterTopology
        declared = configuration.get('topology') or {
            'tools': [{'id': 'T0', 'nozzle_mm': .4}],
            'positions': [{'id': 'S0', 'filament': {'type': 'PLA'}}], 'swapping': False}
        sizes = configuration.get('nozzle_sizes_mm')
        if sizes is not None:
            if len(sizes) != len(declared['tools']):
                raise ValueError('Provide one size per virtual tool')
            declared = {**declared, 'tools': [{**tool, 'nozzle_mm': sizes[i]} for i, tool in enumerate(declared['tools'])]}
        if 'positions' in configuration:
            declared = {**declared, 'positions': configuration['positions'], 'swapping': len(configuration['positions']) > 1}
        return PrinterTopology.model_validate(declared)

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
            axis_jog=True, home_axes=True, nozzle_temp=True, chamber_temp=True, direct_upload=True,
        )

    @property
    def file_upload_supported(self) -> bool:
        return True

    def upload_file(self, data: bytes, filename: str) -> bool:
        return True

    def set_chamber_temp(self, celsius: int) -> bool:
        return True
