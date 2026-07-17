from __future__ import annotations
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Optional


@dataclass
class PrinterCapabilities:
    ams: bool = False
    file_upload: bool = False
    bed_levelling: bool = False
    flow_calibration: bool = False
    vibration_cali: bool = False
    layer_inspect: bool = False
    timelapse: bool = False
    chamber_light: bool = False
    gcode: bool = False
    pause_resume: bool = False
    skip_objects: bool = False
    multi_nozzle: bool = False
    file_models: bool = False
    file_history: bool = False
    file_timelapse: bool = False
    camera: bool = False
    fan_control: bool = False
    temp_control: bool = False


@dataclass
class StartPrintOptions:
    plate_id: int = 1
    ams_mapping: list[int] | None = None
    bed_levelling: bool = True
    flow_cali: bool = False
    vibration_cali: bool = True
    layer_inspect: bool = False
    timelapse: bool = False
    use_ams: bool = True
    gcode_path: str | None = None


@dataclass
class PrinterFile:
    id: str
    name: str
    size: int
    modified_at: str | None = None


@dataclass
class ConnectionField:
    name: str
    label: str
    field_type: str  # "text" | "password" | "number"
    required: bool = True
    default: str | int | None = None
    placeholder: str = ""
    help_text: str = ""


class AbstractPrinterClient(ABC):
    printer_type: ClassVar[str]

    # --- Connection lifecycle (must implement) ---

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @abstractmethod
    def connect(self, loop=None) -> None: ...

    @abstractmethod
    def disconnect(self, timeout: int = 0) -> None: ...

    # --- Print control (must implement) ---

    @abstractmethod
    def start_print(self, file_name: str, options: StartPrintOptions | None = None) -> bool: ...

    @abstractmethod
    def stop_print(self) -> bool: ...

    @abstractmethod
    def pause_print(self) -> bool: ...

    @abstractmethod
    def resume_print(self) -> bool: ...

    # --- Command interface ---

    @abstractmethod
    def send_gcode(self, gcode: str) -> bool: ...

    @abstractmethod
    def request_status_update(self) -> None: ...

    def home(self) -> bool:
        return self.send_gcode("G28")

    def jog_z(self, distance_mm: float, force: bool = False) -> bool:
        if force:
            self.send_gcode("M211 S0")
        self.send_gcode("G91")
        self.send_gcode(f"G1 Z{distance_mm}")
        self.send_gcode("G90")
        if force:
            self.send_gcode("M211 S1")
        return True

    def set_chamber_light(self, on: bool) -> bool:
        return False

    def set_fan_speeds(self, model_pct: int, aux_pct: int, box_pct: int) -> bool:
        return False

    def set_bed_temp(self, celsius: int) -> bool:
        return False

    @property
    def gcode_supported(self) -> bool:
        return True

    # --- Slicing output contract (overridable per vendor) ---

    def orca_export_args(self, file_base: str) -> list[str]:
        """Extra OrcaSlicer CLI args declaring this printer's print artifact.

        OrcaSlicer always writes raw gcode to ``--outputdir``; ``--export-3mf``
        additionally emits the archive. Default ([]) → the printer prints raw
        ``.gcode`` (Klipper/Centauri). Vendors whose printers ingest the sliced
        3MF (e.g. Bambu) override to return ``["--export-3mf", f"{file_base}.gcode.3mf"]``.
        ``file_base`` is a meaningful, job-derived name (no extension).
        """
        return []

    # --- Capabilities and lifecycle hooks ---

    def get_capabilities(self) -> PrinterCapabilities:
        return PrinterCapabilities()

    @property
    def is_idle(self) -> bool:
        return False

    @property
    def is_printing(self) -> bool:
        return False

    # --- Connection field descriptor (classmethod) ---

    @classmethod
    def connection_fields(cls) -> list[ConnectionField]:
        return []

    # --- File management (optional no-ops) ---

    @property
    def file_upload_supported(self) -> bool:
        return False

    @property
    def camera_mjpeg_url(self) -> str | None:
        return None

    @property
    def camera_rtsp_url(self) -> str | None:
        return None

    def control_endpoint(self) -> tuple[str, int] | None:
        """Host/port of the primary control channel, used by the add-printer
        'test connection' to give a useful reachability hint on failure.
        None ⇒ no probe."""
        return None

    def upload_file(self, data: bytes, filename: str) -> bool:
        return False

    def list_files(self, directory: str = "/") -> list[PrinterFile]:
        return []

    def storage_info(self) -> dict | None:
        return None

    def get_loaded_filaments(self) -> list:
        return []

    def remap_sliceable_3mf(self, sliceable_3mf, *, tool_index=None, filament_map=None) -> None:
        """Rewrite the prepared sliceable 3MF in place to route the model's filament(s)
        to the chosen physical tool(s). Default: no-op (vendors that realize the mapping
        elsewhere, e.g. Bambu at print time via ams_mapping)."""
        return None

    # --- File ID validation (call before any external file_id input) ---

    def _validate_file_id(self, file_id: str) -> None:
        decoded = file_id
        for _ in range(10):
            new = urllib.parse.unquote(decoded)
            if new == decoded:
                break
            decoded = new
        if any(c in decoded for c in ("\x00", "\n", "\r")):
            raise ValueError(f"Invalid file_id: {file_id!r}")
        if ".." in decoded:
            raise ValueError(f"Invalid file_id (path traversal): {file_id!r}")
        if decoded.startswith("/") or decoded.startswith("~"):
            raise ValueError(f"Invalid file_id (absolute path): {file_id!r}")
        if decoded.startswith("\\\\"):
            raise ValueError(f"Invalid file_id (UNC path): {file_id!r}")
        if len(decoded) >= 2 and decoded[1] == ":":
            raise ValueError(f"Invalid file_id (Windows drive): {file_id!r}")
