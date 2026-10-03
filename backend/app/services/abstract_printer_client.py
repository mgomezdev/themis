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
    # Console (BIZ-164). Z jog, home-all and the bed setpoint are the baseline; these gate the rest.
    axis_jog: bool = False        # X/Y jog
    home_axes: bool = False       # home a single axis
    nozzle_temp: bool = False     # nozzle setpoint
    chamber_temp: bool = False    # chamber setpoint
    direct_upload: bool = False   # upload (and optionally start) a file outside the queue
    # File browser (BIZ-169): list what is stored on the printer; delete / download are separate because
    # not every protocol exposes them.
    file_browser: bool = False
    file_delete: bool = False
    file_download: bool = False


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


class FileTooLargeError(Exception):
    """A download was aborted because it would exceed the caller's size cap."""


SEVERITIES = ("info", "warning", "error", "fatal")           # ascending


@dataclass(frozen=True)
class Alarm:
    """A problem a printer currently reports, in the shape every vendor is normalised to. `code` is the stable
    identity (a printer re-reporting the same code is the same alarm); `message` is human text."""
    code: str
    severity: str                       # one of SEVERITIES
    message: str
    source: str | None = None           # "hms" | "klipper" | "sdcp" …
    help_url: str | None = None


@dataclass
class DiscoveredPrinter:
    """A printer found on the network, ready to pre-fill the add form (secrets such as access codes stay blank)."""
    printer_type: str
    ip: str
    model: str | None = None
    name: str | None = None
    serial: str | None = None
    connection_config: dict = field(default_factory=dict)
    note: str | None = None                  # e.g. "Requires an API key"


@dataclass
class PrinterFile:
    """One entry in a printer's storage. `id` is what the other file operations (print/delete/download) take
    back — a path in the vendor's own addressing — while `name` is for display."""
    id: str
    name: str
    size: int
    modified_at: str | None = None          # UTC ISO-8601 when the protocol reports it
    is_dir: bool = False
    metadata: dict | None = None            # {"estimated_seconds", "filament_grams", "filament_mm", "slicer"} when known


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

    def jog(self, axis: str, distance_mm: float) -> bool:
        """Relative move of one axis (X/Y/Z). Default: G-code; vendors with a native command override."""
        axis = axis.upper()
        if not self.send_gcode("G91"):
            return False
        try:
            return bool(self.send_gcode(f"G1 {axis}{distance_mm}"))
        finally:
            self.send_gcode("G90")      # never leave the printer in relative mode

    def home_axes(self, axes: str) -> bool:
        """Home specific axes (e.g. 'X', 'XY'). Default: G28 with the listed axes."""
        return self.send_gcode("G28 " + " ".join(axes.upper()))

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

    def set_nozzle_temp(self, celsius: int) -> bool:
        return self.send_gcode(f"M104 S{int(celsius)}") if self.gcode_supported else False

    def set_chamber_temp(self, celsius: int) -> bool:
        return False

    @property
    def gcode_supported(self) -> bool:
        return True

    # --- Slicing output contract (overridable per vendor) ---

    # Whether the printer can be handed a plain .gcode file (pre-sliced job, BIZ-188). Vendors that only ingest a
    # sliced archive (Bambu: .gcode.3mf) set this False until a raw-gcode start has been verified on hardware.
    raw_gcode_supported: bool = True

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

    # --- Alarms (BIZ-157) ---

    def get_alarms(self) -> list[Alarm]:
        """The problems the printer reports RIGHT NOW (empty = healthy). Called on every state update, so it must
        be a cheap read of already-parsed state. Default: this vendor reports none."""
        return []

    # --- Network discovery (classmethods; BIZ-153) ---

    @classmethod
    async def discover_host(cls, net, ip: str) -> DiscoveredPrinter | None:
        """Probe ONE address for this vendor's printer using its documented discovery signature. `net` is a
        `discovery_net.Network`. Default: this vendor can't be discovered."""
        return None

    @classmethod
    def parse_announcement(cls, ip: str, datagram: bytes) -> DiscoveredPrinter | None:
        """Parse a passively heard multicast announcement (same-subnet discovery). Default: none."""
        return None

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

    def delete_file(self, file_id: str) -> bool:
        return False

    def download_file(self, file_id: str, max_bytes: int | None = None) -> bytes | None:
        """Fetch a stored file's bytes (None = unsupported or failed). Raises FileTooLargeError as soon as more
        than `max_bytes` have arrived, without buffering the rest."""
        return None

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
