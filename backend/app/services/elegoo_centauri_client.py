from __future__ import annotations

import hashlib
import io
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, ClassVar

import httpx
import websocket

from .abstract_printer_client import (
    AbstractPrinterClient,
    ConnectionField,
    PrinterCapabilities,
    PrinterFile,
    StartPrintOptions,
)

logger = logging.getLogger(__name__)


def _as_bool(v) -> bool:
    """Coerce a config value (which arrives as a string from the frontend) to bool."""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


DEFAULT_PORT = 3030
_CAMERA_PORT = 3031
_ACK_TIMEOUT = 10.0
_KEEPALIVE_INTERVAL = 50  # seconds between GET_STATUS keepalives
_RECONNECT_DELAY = 5.0    # seconds to wait before reconnecting after a drop


class _Cmd:
    GET_STATUS = 0
    GET_ATTR = 1
    START_PRINT = 128
    SUSPEND_PRINT = 129
    STOP_PRINT = 130
    RESTORE_PRINT = 131
    GET_FILE_LIST = 258
    DELETE_FILE = 259
    EDIT_VIDEO_STREAMING = 386
    EDIT_AXIS_NUMBER = 401
    EDIT_AXIS_ZERO = 402
    EDIT_STATUS_DATA = 403


# PrintInfo.Status sub-state codes reported by the printer
_PRINT_INFO_STATE: dict[int, str] = {
    0:  "standby",
    1:  "warming_up",
    5:  "pausing",
    6:  "paused",
    8:  "cancelled",
    9:  "complete",
    13: "printing",
    14: "cancelled",
    20: "leveling",
}

# Normalized state strings used by the rest of the app (matches Bambu convention)
_NORM_STATE: dict[str, str] = {
    "standby":   "IDLE",
    "printing":  "RUNNING",
    "warming_up":"RUNNING",
    "leveling":  "RUNNING",
    "pausing":   "RUNNING",
    "paused":    "PAUSE",
    "complete":  "FINISH",
    "cancelled": "FAILED",
}

_TEMP_MAP = {
    "TempOfNozzle":    "nozzle",
    "TempTargetNozzle":"nozzle_target",
    "TempOfHotbed":    "bed",
    "TempTargetHotbed":"bed_target",
    "TempOfBox":       "chamber",
    "TempTargetBox":   "chamber_target",
}


@dataclass
class ElegooState:
    connected: bool = False
    current_status: list = field(default_factory=list)
    print_state: str = "standby"
    filename: str | None = None
    task_id: str | None = None
    progress: float = 0.0
    layer_num: int | None = None
    total_layers: int | None = None
    current_ticks: float = 0.0
    total_ticks: float = 0.0
    print_speed_pct: int = 100
    temperatures: dict = field(default_factory=dict)
    fan_model: int = 0
    fan_aux: int = 0
    fan_box: int = 0
    chamber_light: bool = False
    rgb_light: list = field(default_factory=lambda: [0, 0, 0])
    video_url: str | None = None
    firmware_version: str | None = None
    machine_name: str | None = None
    mainboard_id: str = ""
    raw: dict = field(default_factory=dict, compare=False)

    # Compat shims so generic code that reads Bambu fields doesn't crash
    @property
    def raw_data(self):
        return None

    @property
    def state(self) -> str:
        return _NORM_STATE.get(self.print_state, self.print_state.upper())

    @property
    def current_print(self) -> str | None:
        return self.filename

    @property
    def remaining_time(self) -> int:
        if self.total_ticks > 0 and self.current_ticks < self.total_ticks:
            return int((self.total_ticks - self.current_ticks) / 60)
        return 0


class ElegooCentauriClient(AbstractPrinterClient):
    printer_type: ClassVar[str] = "elegoo_centauri"

    def __init__(
        self,
        ip_address: str,
        port: int = DEFAULT_PORT,
        on_state_change: Callable | None = None,
        on_print_complete: Callable | None = None,
        bed_type: int | str = 4,
        bed_leveling: bool | str = True,
        timelapse: bool | str = False,
    ) -> None:
        self._ip = ip_address
        self._port = int(port)  # coerce — frontend sends strings
        # Per-printer print defaults sent with every start_print (see start_print).
        self._bed_type = int(bed_type)
        self._bed_leveling = _as_bool(bed_leveling)
        self._timelapse = _as_bool(timelapse)
        self._on_state_change = on_state_change
        self._on_print_complete = on_print_complete

        self.state = ElegooState()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._ws: websocket.WebSocketApp | None = None

        self._pending_acks: dict[str, threading.Event] = {}
        self._ack_results: dict[str, int] = {}
        self._response_data: dict[str, dict] = {}

        self._video_url_event = threading.Event()
        self._pending_video_url: str | None = None
        self._prev_print_state: str = "standby"
        self._loop = None

    # ------------------------------------------------------------------
    # ABC metadata
    # ------------------------------------------------------------------

    @classmethod
    def connection_fields(cls) -> list[ConnectionField]:
        return [
            ConnectionField(
                name="ip_address",
                label="IP Address",
                field_type="text",
                placeholder="192.168.1.x",
            ),
            ConnectionField(
                name="port",
                label="Port",
                field_type="number",
                default=DEFAULT_PORT,
                required=False,
            ),
            ConnectionField(
                name="bed_type",
                label="Bed type",
                field_type="number",
                default=4,
                required=False,
                help_text="Elegoo plate type code (PrintPlatformType) sent when starting a print.",
            ),
            ConnectionField(
                name="bed_leveling",
                label="Auto bed leveling",
                field_type="number",
                default=1,
                required=False,
                help_text="1 = run bed-flatness calibration before each print, 0 = skip.",
            ),
            ConnectionField(
                name="timelapse",
                label="Timelapse",
                field_type="number",
                default=0,
                required=False,
                help_text="1 = record a timelapse during the print, 0 = off.",
            ),
        ]

    def get_capabilities(self) -> PrinterCapabilities:
        return PrinterCapabilities(
            camera=True,
            pause_resume=True,
            chamber_light=True,
            bed_levelling=True,
            file_upload=True,
            file_models=True,
            file_history=True,
            gcode=False,
            fan_control=True,
            temp_control=True,
        )

    # ------------------------------------------------------------------
    # State properties
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        with self._lock:
            return self.state.connected

    @property
    def is_idle(self) -> bool:
        with self._lock:
            return self.state.print_state in ("standby", "complete", "cancelled")

    @property
    def is_printing(self) -> bool:
        with self._lock:
            return self.state.print_state in ("printing", "warming_up", "leveling", "pausing")

    @property
    def camera_mjpeg_url(self) -> str | None:
        # Centauri Carbon camera is always at port 3031/video; use video_url if
        # the printer returned one via Cmd 386, otherwise use the known fallback.
        with self._lock:
            return self.state.video_url or f"http://{self._ip}:{_CAMERA_PORT}/video"

    @property
    def camera_rtsp_url(self) -> str | None:
        return None

    @property
    def file_upload_supported(self) -> bool:
        return True

    @property
    def gcode_supported(self) -> bool:
        return False

    def check_staleness(self) -> bool:
        return self.connected

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self, loop=None) -> None:
        self._loop = loop
        self._stop_event.clear()
        threading.Thread(
            target=self._run_ws,
            name=f"elegoo-{self._ip}",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._run_sdcp_keepalive,
            name=f"elegoo-ka-{self._ip}",
            daemon=True,
        ).start()

    def _run_ws(self) -> None:
        url = f"ws://{self._ip}:{self._port}/websocket"
        while not self._stop_event.is_set():
            ws = websocket.WebSocketApp(
                url,
                on_open=self._on_ws_open,
                on_message=self._on_ws_message,
                on_close=self._on_ws_close,
                on_error=self._on_ws_error,
            )
            self._ws = ws
            ws.run_forever(ping_interval=30, ping_timeout=10)
            self._ws = None
            if not self._stop_event.is_set():
                self._stop_event.wait(_RECONNECT_DELAY)

    def _run_sdcp_keepalive(self) -> None:
        while not self._stop_event.wait(_KEEPALIVE_INTERVAL):
            if self._ws is not None:
                self._send(_Cmd.GET_STATUS, {}, wait_ack=False)

    def disconnect(self, timeout: int = 0) -> None:
        self._stop_event.set()
        ws = self._ws
        if ws:
            ws.close()
        with self._lock:
            self.state.connected = False
        # Unblock any threads waiting on ACKs
        for event in list(self._pending_acks.values()):
            event.set()

    # ------------------------------------------------------------------
    # WebSocket event handlers
    # ------------------------------------------------------------------

    def _on_ws_open(self, ws) -> None:
        # Request current state and static attributes immediately on connect
        self._send(_Cmd.GET_STATUS, {}, wait_ack=False)
        self._send(_Cmd.GET_ATTR, {}, wait_ack=False)

    def _on_ws_close(self, ws, close_status_code, close_msg) -> None:
        with self._lock:
            was_connected = self.state.connected
            self.state.connected = False
        if was_connected and self._on_state_change and self._loop:
            import asyncio
            asyncio.run_coroutine_threadsafe(
                self._on_state_change(self.state), self._loop
            )

    def _on_ws_error(self, ws, error) -> None:
        pass  # run_forever handles reconnect via _on_ws_close

    def _on_ws_message(self, ws, message: str) -> None:
        try:
            data = json.loads(message)
        except Exception:
            return
        topic = data.get("Topic", "")
        if "sdcp/status/" in topic or ("Status" in data and "Topic" not in data):
            self._parse_status_msg(data)
        elif "sdcp/attributes/" in topic:
            self._parse_attr_msg(data)
        elif "sdcp/response/" in topic or "sdcp/error/" in topic:
            self._parse_response_msg(data)
        elif "Status" in data:
            # Topic present but doesn't match known prefixes — still a status push
            self._parse_status_msg(data)

    # ------------------------------------------------------------------
    # Message parsers
    # ------------------------------------------------------------------

    def _parse_status_msg(self, data: dict) -> None:
        status = data.get("Status") or {}
        if not status:
            return

        new = ElegooState()

        # Carry forward fields that only arrive in GET_ATTR responses
        with self._lock:
            new.firmware_version = self.state.firmware_version
            new.machine_name = self.state.machine_name
            new.mainboard_id = self.state.mainboard_id
            new.rgb_light = list(self.state.rgb_light)
            new.video_url = self.state.video_url

        new.connected = True
        new.raw = data

        # CurrentStatus is a list of active state codes; 8 = print complete
        codes = status.get("CurrentStatus", [])
        if not isinstance(codes, list):
            codes = [codes]
        new.current_status = codes
        top = codes[0] if codes else 0

        print_info = status.get("PrintInfo", {})
        pi_status = print_info.get("Status")

        if 8 in codes:
            new.print_state = "complete"
        elif pi_status is not None and pi_status in _PRINT_INFO_STATE:
            new.print_state = _PRINT_INFO_STATE[pi_status]
        elif top == 1:
            new.print_state = "printing"
        else:
            new.print_state = "standby"

        # PrintInfo fields (present during and after prints)
        new.filename = print_info.get("Filename") or None
        new.task_id = print_info.get("TaskId") or None
        new.total_ticks = float(print_info.get("TotalTicks", 0))
        new.current_ticks = float(print_info.get("CurrentTicks", 0))
        total_layers = int(print_info.get("TotalLayer", 0))
        current_layer = int(print_info.get("CurrentLayer", 0))
        new.total_layers = total_layers or None
        new.layer_num = current_layer or None
        if new.total_ticks > 0:
            new.progress = min(new.current_ticks / new.total_ticks * 100.0, 100.0)
        else:
            new.progress = float(print_info.get("Progress", 0))
        new.print_speed_pct = int(print_info.get("PrintSpeed", 100))

        # Temperatures
        temps = {}
        for sdcp_key, norm_key in _TEMP_MAP.items():
            if sdcp_key in status:
                temps[norm_key] = round(float(status[sdcp_key]), 1)
        new.temperatures = temps

        # Fans
        fans = status.get("CurrentFanSpeed", {})
        new.fan_model = int(fans.get("ModelFan", 0))
        new.fan_aux = int(fans.get("AuxiliaryFan", 0))
        new.fan_box = int(fans.get("BoxFan", 0))

        # Lights — RgbLight only updated when present to preserve accent color
        lights = status.get("LightStatus", {})
        new.chamber_light = bool(lights.get("SecondLight", False))
        if "RgbLight" in lights:
            new.rgb_light = list(lights["RgbLight"])

        # Mainboard ID embedded in envelope
        envelope_d = data.get("Data", {})
        if envelope_d.get("MainboardID"):
            new.mainboard_id = envelope_d["MainboardID"]

        with self._lock:
            prev = self.state
            self.state = new

        if self._loop:
            import asyncio
            if new != prev and self._on_state_change:
                asyncio.run_coroutine_threadsafe(
                    self._on_state_change(new), self._loop
                )
            if (
                new.print_state == "complete"
                and self._prev_print_state != "complete"
                and self._on_print_complete
            ):
                asyncio.run_coroutine_threadsafe(
                    self._on_print_complete(new), self._loop
                )
        self._prev_print_state = new.print_state

    def _parse_attr_msg(self, data: dict) -> None:
        # Attributes arrive either top-level or nested under Data.Data
        attrs = data.get("Attributes") or data.get("Data", {}).get("Data", {})
        if not attrs:
            return
        with self._lock:
            if attrs.get("MainboardID"):
                self.state.mainboard_id = attrs["MainboardID"]
            if attrs.get("FirmwareVersion"):
                self.state.firmware_version = attrs["FirmwareVersion"]
            if attrs.get("MachineName"):
                self.state.machine_name = attrs["MachineName"]

    def _parse_response_msg(self, data: dict) -> None:
        d = data.get("Data", {})
        # Printer appends \x01 to RequestID in responses
        raw_rid = d.get("RequestID", "")
        request_id = raw_rid.rstrip("\x01")

        if d.get("MainboardID"):
            with self._lock:
                self.state.mainboard_id = d["MainboardID"]

        inner = d.get("Data", {})
        # Result code lives at the envelope top level for most commands, but
        # print-control acks (start/stop/pause) nest it as Data.Data.Ack.
        inner_ack = inner.get("Ack", -1) if isinstance(inner, dict) else -1
        ack = d.get("Result", d.get("Ack", inner_ack))

        # Cmd 386 (EDIT_VIDEO_STREAMING) response carries the live stream URL
        if d.get("Cmd") == _Cmd.EDIT_VIDEO_STREAMING:
            video_url = (inner.get("VideoUrl") or "").strip()
            if not video_url.startswith("http"):
                video_url = f"http://{self._ip}:{_CAMERA_PORT}/video"
            with self._lock:
                self.state.video_url = video_url
            self._pending_video_url = video_url
            self._video_url_event.set()

        if request_id in self._pending_acks:
            self._ack_results[request_id] = ack
            self._response_data[request_id] = inner
            self._pending_acks[request_id].set()

    # ------------------------------------------------------------------
    # Send helpers
    # ------------------------------------------------------------------

    def _send(self, cmd: int, data: dict, wait_ack: bool = True) -> bool:
        ws = self._ws
        if not ws:
            return False
        request_id = uuid.uuid4().hex
        with self._lock:
            mainboard_id = self.state.mainboard_id
        payload = {
            "Id": "",
            "Data": {
                "Cmd": cmd,
                "Data": data,
                "RequestID": request_id,
                "MainboardID": mainboard_id,
                "TimeStamp": int(time.time()),
                "From": 1,
            },
        }
        if wait_ack:
            event = threading.Event()
            self._pending_acks[request_id] = event
        try:
            ws.send(json.dumps(payload))
        except Exception:
            self._pending_acks.pop(request_id, None)
            return False
        if not wait_ack:
            return True
        fired = event.wait(timeout=_ACK_TIMEOUT)
        result = self._ack_results.pop(request_id, -1)
        self._pending_acks.pop(request_id, None)
        self._response_data.pop(request_id, None)
        return fired and result == 0

    def _send_with_response(self, cmd: int, data: dict) -> tuple[bool, dict]:
        """Send a command and return (success, response_data_dict)."""
        ws = self._ws
        if not ws:
            return False, {}
        request_id = uuid.uuid4().hex
        with self._lock:
            mainboard_id = self.state.mainboard_id
        payload = {
            "Id": "",
            "Data": {
                "Cmd": cmd,
                "Data": data,
                "RequestID": request_id,
                "MainboardID": mainboard_id,
                "TimeStamp": int(time.time()),
                "From": 1,
            },
        }
        event = threading.Event()
        self._pending_acks[request_id] = event
        try:
            ws.send(json.dumps(payload))
        except Exception:
            self._pending_acks.pop(request_id, None)
            return False, {}
        fired = event.wait(timeout=_ACK_TIMEOUT)
        result = self._ack_results.pop(request_id, -1)
        resp = self._response_data.pop(request_id, {})
        self._pending_acks.pop(request_id, None)
        return fired and result == 0, resp

    # ------------------------------------------------------------------
    # Print control
    # ------------------------------------------------------------------

    def request_status_update(self) -> None:
        self._send(_Cmd.GET_STATUS, {}, wait_ack=False)

    def send_gcode(self, gcode: str) -> bool:
        return False  # SDCP has no raw G-code channel

    def start_print(self, file_name: str, options: StartPrintOptions | None = None) -> bool:
        # The Centauri SDCP START_PRINT needs the on-printer "/local/" path plus the
        # print parameters; a bare filename is acked but the print never starts.
        # (Matches OrcaSlicer's ElegooLink: Filename, StartLayer, Calibration_switch,
        # PrintPlatformType, Tlp_Switch.) Bed leveling / type / timelapse are
        # per-printer config (see connection_fields).
        filename = file_name if file_name.startswith("/") else f"/local/{file_name}"
        return self._send(_Cmd.START_PRINT, {
            "Filename": filename,
            "StartLayer": 0,
            "Calibration_switch": 1 if self._bed_leveling else 0,
            "PrintPlatformType": self._bed_type,
            "Tlp_Switch": 1 if self._timelapse else 0,
        })

    def stop_print(self) -> bool:
        return self._send(_Cmd.STOP_PRINT, {})

    def pause_print(self) -> bool:
        return self._send(_Cmd.SUSPEND_PRINT, {})

    def resume_print(self) -> bool:
        return self._send(_Cmd.RESTORE_PRINT, {})

    # ------------------------------------------------------------------
    # Axis control (native SDCP, not G-code)
    # ------------------------------------------------------------------

    def home(self) -> bool:
        return self._send(_Cmd.EDIT_AXIS_ZERO, {"Axis": "XYZ"})

    def jog_z(self, distance_mm: float, force: bool = False) -> bool:
        return self._send(_Cmd.EDIT_AXIS_NUMBER, {"Axis": "Z", "Step": distance_mm})

    # ------------------------------------------------------------------
    # Chamber light
    # ------------------------------------------------------------------

    def set_chamber_light(self, on: bool) -> bool:
        with self._lock:
            rgb = list(self.state.rgb_light)
        success = self._send(
            _Cmd.EDIT_STATUS_DATA,
            {"LightStatus": {"SecondLight": on, "RgbLight": rgb}},
        )
        if success:
            with self._lock:
                self.state.chamber_light = on
        return success

    def set_fan_speeds(self, model_pct: int, aux_pct: int, box_pct: int) -> bool:
        return self._send(
            _Cmd.EDIT_STATUS_DATA,
            {"TargetFanSpeed": {"ModelFan": model_pct, "AuxiliaryFan": aux_pct, "BoxFan": box_pct}},
        )

    def set_bed_temp(self, celsius: int) -> bool:
        return self._send(_Cmd.EDIT_STATUS_DATA, {"TempTargetHotbed": celsius})

    # ------------------------------------------------------------------
    # Video streaming (Cmd 386)
    # ------------------------------------------------------------------

    def start_video_stream(self, timeout: float = 5.0) -> str:
        """Enable the MJPEG stream and return its URL."""
        self._video_url_event.clear()
        self._pending_video_url = None
        self._send(_Cmd.EDIT_VIDEO_STREAMING, {"Enable": 1}, wait_ack=False)
        self._video_url_event.wait(timeout=timeout)
        fallback = f"http://{self._ip}:{_CAMERA_PORT}/video"
        url = self._pending_video_url or fallback
        return f"{url}?t={int(time.time())}"

    def ping_video_stream(self) -> None:
        """Reset the printer's 60-second stream inactivity timer."""
        self._send(_Cmd.EDIT_VIDEO_STREAMING, {"Enable": 1}, wait_ack=False)

    def stop_video_stream(self) -> None:
        self._send(_Cmd.EDIT_VIDEO_STREAMING, {"Enable": 0}, wait_ack=False)

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------

    def upload_file(self, data: bytes, filename: str) -> bool:
        url = f"http://{self._ip}:{self._port}/uploadFile/upload"
        md5 = hashlib.md5(data).hexdigest()
        chunk_size = 1024 * 1024  # 1MB per packet maximum as per SDCP spec
        total_size = len(data)
        transfer_uuid = uuid.uuid4().hex

        offset = 0
        while offset < total_size:
            chunk = data[offset:offset + chunk_size]
            try:
                resp = httpx.post(
                    url,
                    data={
                        "TotalSize": str(total_size),
                        "Uuid": transfer_uuid,
                        "Offset": str(offset),
                        "Check": "1",
                        "S-File-MD5": md5,
                    },
                    files={"File": (filename, io.BytesIO(chunk), "application/octet-stream")},
                    timeout=120.0,
                )
            except Exception:
                logger.exception("Upload POST to %s failed at offset %d (%s, %d bytes) — printer HTTP "
                                 "endpoint unreachable/refused", url, offset, filename, total_size)
                return False
            try:
                result = resp.json()
            except Exception:
                logger.warning("Upload of %s at offset %d: non-JSON response (HTTP %s): %s",
                               filename, offset, resp.status_code, resp.text[:300])
                return False
            if result.get("success") is not True and result.get("code") != "000000":
                logger.warning("Upload of %s at offset %d rejected by printer (HTTP %s): %s",
                               filename, offset, resp.status_code, result)
                return False
            offset += len(chunk)
        return True

    def list_files(self, directory: str = "/") -> list[PrinterFile]:
        if directory == "/":
            directory = "/local/"
        ok, resp = self._send_with_response(_Cmd.GET_FILE_LIST, {"Url": directory})
        if not ok:
            return []
        return [
            PrinterFile(
                id=f.get("name", ""),
                name=f.get("name", ""),
                size=int(f.get("size", 0)),
            )
            for f in resp.get("FileList", [])
        ]

    def delete_file(self, remote_path: str) -> bool:
        return self._send(_Cmd.DELETE_FILE, {"FileList": [remote_path], "FolderList": []})

    def get_loaded_filaments(self) -> list:
        return [{
            "type": "", "color": "#808080",
            "tray_info_idx": "", "tray_sub_brands": "",
            "extruder_id": None, "is_external": True,
        }]
