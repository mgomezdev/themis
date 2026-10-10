"""Moonraker/Klipper transport shared by every Moonraker-protocol printer plugin (BIZ-148).

Status streams over the Moonraker WebSocket (`printer.objects.subscribe`, JSON-RPC); control and files go over Moonraker HTTP
(`httpx`). The protocol-level behaviour lives here once; a plugin subclasses it for what is genuinely its own — the printer-type
key, how many toolheads/extruders it has, vendor-only Klipper objects (`_extra_objects` / `_apply_extra`), the camera and its
capabilities. Everything optional (fans, chamber, cameras, filament sensors, extra extruders) is tolerated when absent: Moonraker
simply omits unknown objects, and nothing here treats a missing one as a failure.

Authoritative signals: `print_stats.state` (standby/printing/paused/complete/cancelled/error) is the print state; the transition to
`complete` fires the print-complete callback exactly once; `webhooks.state` + `print_stats.message` feed the normalised alarms."""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, ClassVar

import httpx
import websocket

from ..abstract_printer_client import (
    AbstractPrinterClient, Alarm, ConnectionField, DiscoveredPrinter, FileTooLargeError, PrinterCapabilities, PrinterFile,
    StartPrintOptions,
)
from . import files as mfiles
from .alarms import klipper_alarms

logger = logging.getLogger(__name__)

DEFAULT_PORT = 7125
RECONNECT_DELAY = 5.0
# Klipper print_stats.state -> Themis normalized state string.
NORM_STATE = {
    "standby": "IDLE",
    "printing": "RUNNING",
    "paused": "PAUSE",
    "complete": "FINISH",
    "cancelled": "FAILED",
    "error": "FAILED",
}
MAX_EXTRUDERS = 8


def extruder_names(count: int) -> tuple[str, ...]:
    """Klipper object names of the first `count` extruders: extruder, extruder1, extruder2…"""
    count = max(1, min(int(count or 1), MAX_EXTRUDERS))
    return tuple("extruder" if i == 0 else f"extruder{i}" for i in range(count))


@dataclass
class MoonrakerState:
    connected: bool = False
    klippy_ready: bool = False
    print_state: str = "standby"          # raw Klipper print_stats.state
    filename: str | None = None
    progress: float = 0.0                 # 0..1 (display_status.progress)
    print_duration: float = 0.0
    layer_num: int = 0
    total_layers: int = 0
    bed_temp: float = 0.0
    bed_target: float = 0.0
    extruder_temps: list = field(default_factory=lambda: [0.0])
    extruder_targets: list = field(default_factory=lambda: [0.0])
    active_extruder: int = 0
    fan_speed: float | None = None        # part-cooling fan 0..1, None = the printer has no `fan` object
    klippy_state: str | None = None       # webhooks.state
    klippy_message: str | None = None     # webhooks.state_message
    print_message: str | None = None      # print_stats.message
    raw: dict = field(default_factory=dict)

    @property
    def state(self) -> str:
        return NORM_STATE.get(self.print_state, self.print_state.upper())

    @property
    def current_print(self) -> str | None:
        return self.filename or None

    @property
    def remaining_time(self) -> int:
        if 0.001 < self.progress < 1.0:
            return int(self.print_duration * (1.0 - self.progress) / self.progress / 60.0)
        return 0

    @property
    def temperatures(self) -> dict:
        n = len(self.extruder_temps)
        i = self.active_extruder if 0 <= self.active_extruder < n else 0
        return {
            "nozzle": self.extruder_temps[i],
            "nozzle_target": self.extruder_targets[i],
            "bed": self.bed_temp,
            "bed_target": self.bed_target,
            "extruders": [{"index": j, "temp": self.extruder_temps[j], "target": self.extruder_targets[j]} for j in range(n)],
        }


def serialize_moonraker(state, printer_id: int, printer_type: str) -> dict:
    conn = bool(getattr(state, "connected", False) and getattr(state, "klippy_ready", False))
    fan = getattr(state, "fan_speed", None)
    return {
        "printer_type": printer_type,
        "id": printer_id,
        "connected": conn,
        "state": getattr(state, "state", "unknown"),
        "current_print": getattr(state, "current_print", None),
        "progress": getattr(state, "progress", 0.0) * 100.0,  # Klipper display_status.progress is 0..1; the API is 0..100
        "remaining_time": getattr(state, "remaining_time", 0) or 0,
        "layer_num": getattr(state, "layer_num", 0),
        "total_layers": getattr(state, "total_layers", 0),
        "temperatures": getattr(state, "temperatures", {}),
        "fan_model": int(round(fan * 100)) if fan is not None else 0,
        "fan_aux": 0,
        "fan_box": 0,
        "speed_factor": 1.0,
        "klippy_state": "ready" if conn else "disconnected",
        "cover_url": None,
    }


class MoonrakerClientBase(AbstractPrinterClient):
    """Generic Moonraker client. Subclasses set `printer_type` and may override the hooks marked below."""

    # Log/thread label ("Snapmaker", "Moonraker"…).
    label: ClassVar[str] = "Moonraker"

    def __init__(
        self,
        ip_address: str,
        port: int | str = DEFAULT_PORT,
        api_key: str | None = None,
        on_state_change: Callable | None = None,
        on_print_complete: Callable | None = None,
        toolheads: int | str = 1,
    ) -> None:
        self._ip = ip_address
        self._port = int(port) if port else DEFAULT_PORT
        self._api_key = (api_key or "").strip() or None
        try:
            n = int(toolheads or 1)
        except (TypeError, ValueError):
            n = 1
        self._extruders = extruder_names(n)
        self._on_state_change = on_state_change
        self._on_print_complete = on_print_complete
        self._on_ams_change = None          # wired by PrinterManager.connect_printer: persists loaded_filaments
        self.state = self._new_state()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._ws: websocket.WebSocketApp | None = None
        self._loop = None
        self._prev_print_state = "standby"
        self._rpc_id = itertools.count(1)
        self._webcam_url: str | None = None   # discovered via server.webcams.list (None = none advertised / not asked yet)

    # ---- hooks for subclasses ----
    def _new_state(self) -> MoonrakerState:
        n = len(self._extruders)
        return MoonrakerState(extruder_temps=[0.0] * n, extruder_targets=[0.0] * n)

    def _extra_objects(self) -> dict:
        """Vendor-only Klipper objects to subscribe to besides the standard ones."""
        return {}

    def _apply_extra(self, status: dict) -> bool:
        """Apply vendor-only parts of a status update (caller holds the lock). True = loaded filaments changed."""
        return False

    def _discovery_model(self) -> str:
        return "Moonraker / Klipper"

    # ---- standard subscription ----
    def _subscribe_objects(self) -> dict:
        objs = {"print_stats": None, "webhooks": None, "display_status": None, "heater_bed": None, "toolhead": None, "fan": None}
        objs.update({name: None for name in self._extruders})
        objs.update(self._extra_objects())
        return objs

    def get_alarms(self) -> list[Alarm]:
        with self._lock:
            s = self.state
            return klipper_alarms({"state": s.klippy_state, "state_message": s.klippy_message},
                                  {"state": s.print_state, "message": s.print_message})

    # ---- discovery (Moonraker web API: GET /server/info, GET /printer/info) ----
    @classmethod
    async def _discover_moonraker(cls, net, ip: str, model: str) -> DiscoveredPrinter | None:
        """Moonraker answers `GET /server/info` on :7125 with `result.moonraker_version`; with an API key configured
        and the caller untrusted it answers 401/403 — still a Moonraker, flagged as needing a key."""
        status, body = await net.http_get_json(f"http://{ip}:{DEFAULT_PORT}/server/info", 1.0)
        cfg = {"ip_address": ip, "port": DEFAULT_PORT}
        if status in (401, 403):
            return DiscoveredPrinter(printer_type=cls.printer_type, ip=ip, model=model, connection_config=cfg,
                                     note="Requires an API key")
        result = (body or {}).get("result")
        if status != 200 or not isinstance(result, dict) or "moonraker_version" not in result:
            return None
        _s, info = await net.http_get_json(f"http://{ip}:{DEFAULT_PORT}/printer/info", 1.0)
        hostname = ((info or {}).get("result") or {}).get("hostname")
        return DiscoveredPrinter(printer_type=cls.printer_type, ip=ip, model=model, name=hostname, connection_config=cfg)

    @classmethod
    def base_connection_fields(cls) -> list[ConnectionField]:
        return [
            ConnectionField(name="ip_address", label="IP Address", field_type="text", placeholder="192.168.0.x"),
            ConnectionField(name="port", label="Moonraker port", field_type="number", default=DEFAULT_PORT, required=False),
            ConnectionField(name="api_key", label="API key", field_type="password", required=False,
                            help_text="Only if Moonraker requires an API key; leave blank for an open LAN printer."),
        ]

    @classmethod
    def connection_fields(cls) -> list[ConnectionField]:
        return cls.base_connection_fields()

    def serialize_state(self, printer_id: int) -> dict:
        return serialize_moonraker(self.state, printer_id, self.printer_type)

    def get_capabilities(self) -> PrinterCapabilities:
        multi = len(self._extruders) > 1
        with self._lock:
            has_fan = self.state.fan_speed is not None
        return PrinterCapabilities(
            pause_resume=True, gcode=True, camera=self.camera_configured, temp_control=True,
            axis_jog=True, home_axes=True, nozzle_temp=True, direct_upload=True,
            file_browser=True, file_delete=True, file_download=True,
            multi_nozzle=multi, fan_control=has_fan,
        )

    # ---- state properties ----
    @property
    def connected(self) -> bool:
        with self._lock:
            return self.state.connected and self.state.klippy_ready

    @property
    def is_idle(self) -> bool:
        with self._lock:
            return self.state.print_state in ("standby", "complete", "cancelled")

    @property
    def is_printing(self) -> bool:
        with self._lock:
            return self.state.print_state in ("printing", "paused")

    @property
    def file_upload_supported(self) -> bool:
        return True

    @property
    def camera_mjpeg_url(self) -> str | None:
        return self._webcam_url

    @property
    def camera_rtsp_url(self) -> str | None:
        return None

    def control_endpoint(self) -> tuple[str, int]:
        return (self._ip, self._port)

    # ---- HTTP helpers ----
    @property
    def _http_base(self) -> str:
        return f"http://{self._ip}:{self._port}"

    def _headers(self) -> dict:
        return {"X-Api-Key": self._api_key} if self._api_key else {}

    # ---- connection lifecycle ----
    def connect(self, loop=None) -> None:
        self._loop = loop
        self._stop_event.clear()
        threading.Thread(target=self._run_ws, name=f"{self.label.lower()}-{self._ip}", daemon=True).start()

    def _run_ws(self) -> None:
        url = f"ws://{self._ip}:{self._port}/websocket"
        header = [f"X-Api-Key: {self._api_key}"] if self._api_key else None
        while not self._stop_event.is_set():
            ws = websocket.WebSocketApp(
                url, header=header,
                on_open=self._on_ws_open, on_message=self._on_ws_message,
                on_close=self._on_ws_close, on_error=self._on_ws_error,
            )
            self._ws = ws
            logger.info("%s %s: opening Moonraker WebSocket %s", self.label, self._ip, url)
            ws.run_forever(ping_interval=30, ping_timeout=10)
            self._ws = None
            if not self._stop_event.is_set():
                self._stop_event.wait(RECONNECT_DELAY)

    def disconnect(self, timeout: int = 0) -> None:
        self._stop_event.set()
        ws = self._ws
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        with self._lock:
            self.state.connected = False
            self.state.klippy_ready = False

    # ---- WebSocket JSON-RPC ----
    def _next_id(self) -> int:
        return next(self._rpc_id)

    def _ws_send(self, method: str, params: dict | None = None) -> None:
        ws = self._ws
        if ws is None:
            return
        msg = {"jsonrpc": "2.0", "method": method, "id": self._next_id()}
        if params is not None:
            msg["params"] = params
        try:
            ws.send(json.dumps(msg))
        except Exception:
            logger.exception("%s %s: WebSocket send failed (%s)", self.label, self._ip, method)

    def _on_ws_open(self, ws) -> None:
        with self._lock:
            self.state.connected = True
        logger.info("%s %s: Moonraker WebSocket connected", self.label, self._ip)
        self._ws_send("server.info")
        objs = self._subscribe_objects()
        self._ws_send("printer.objects.subscribe", {"objects": objs})
        self._ws_send("printer.objects.query", {"objects": objs})
        self._ws_send("server.webcams.list")       # camera discovery; an install without the webcam component just errors

    def _on_ws_close(self, ws, *_) -> None:
        with self._lock:
            was = self.state.connected
            self.state.connected = False
            self.state.klippy_ready = False
        if was:
            logger.warning("%s %s: Moonraker WebSocket disconnected", self.label, self._ip)
            self._fire_state_change()

    def _on_ws_error(self, ws, error) -> None:
        logger.warning("%s %s: WebSocket error: %s", self.label, self._ip, error)

    def _on_ws_message(self, ws, message: str) -> None:
        try:
            data = json.loads(message)
        except Exception:
            return
        method = data.get("method")
        if method == "notify_status_update":
            params = data.get("params") or []
            if params and isinstance(params[0], dict):
                self._apply_status(params[0])
        elif method == "notify_klippy_ready":
            with self._lock:
                self.state.klippy_ready = True
                self.state.klippy_state, self.state.klippy_message = "ready", None
            self._fire_state_change()
        elif method in ("notify_klippy_disconnected", "notify_klippy_shutdown"):
            with self._lock:
                self.state.klippy_ready = False
                if method == "notify_klippy_shutdown":
                    self.state.klippy_state = "shutdown"
            self._fire_state_change()
            if method == "notify_klippy_shutdown":
                # Moonraker doesn't push `webhooks` updates once Klippy is gone: ask for the reason (`printer.info`
                # → state + state_message) so the alarm says WHY, not just "shutdown".
                self._ws_send("printer.info", {})
        elif "result" in data:
            result = data["result"]
            if isinstance(result, dict) and "state_message" in result and result.get("state") in ("startup", "ready", "shutdown", "error"):
                with self._lock:                               # `printer.info` reply
                    self.state.klippy_state, self.state.klippy_message = result["state"], result.get("state_message")
                self._fire_state_change()
            if isinstance(result, dict):
                if "klippy_state" in result:
                    with self._lock:
                        self.state.klippy_ready = (result.get("klippy_state") == "ready")
                    self._fire_state_change()
                if "status" in result and isinstance(result["status"], dict):
                    self._apply_status(result["status"])
                if isinstance(result.get("webcams"), list):
                    self._apply_webcams(result["webcams"])

    def _apply_webcams(self, webcams: list) -> None:
        """First enabled webcam's MJPEG stream (Moonraker's `stream_url`, relative to the host). A webcam list with only a
        webrtc-style stream or no entry at all leaves the camera unsupported rather than guessing a URL."""
        url = None
        for cam in webcams:
            if not isinstance(cam, dict) or cam.get("enabled") is False:
                continue
            service = str(cam.get("service") or "").lower()
            stream = cam.get("stream_url")
            if stream and service in ("", "mjpegstreamer", "mjpegstreamer-adaptive", "uv4l-mjpeg"):
                url = stream if str(stream).startswith("http") else f"http://{self._ip}{stream if str(stream).startswith('/') else '/' + str(stream)}"
                break
        self._webcam_url = url

    def _apply_status(self, status: dict) -> None:
        with self._lock:
            self.state.raw = status
            wh = status.get("webhooks")
            if wh:
                if "state" in wh:
                    self.state.klippy_state = wh["state"]
                if "state_message" in wh:
                    self.state.klippy_message = wh["state_message"]
            ps = status.get("print_stats")
            if ps:
                if "message" in ps:
                    self.state.print_message = ps.get("message") or None
                if "state" in ps:
                    self.state.print_state = ps["state"]
                if "filename" in ps:
                    self.state.filename = ps.get("filename") or None
                if "print_duration" in ps:
                    self.state.print_duration = ps.get("print_duration") or 0.0
                info = ps.get("info") or {}
                if info.get("current_layer") is not None:
                    self.state.layer_num = info["current_layer"]
                if info.get("total_layer") is not None:
                    self.state.total_layers = info["total_layer"]
            ds = status.get("display_status")
            if ds and ds.get("progress") is not None:
                self.state.progress = ds["progress"]
            hb = status.get("heater_bed")
            if hb:
                if "temperature" in hb:
                    self.state.bed_temp = hb["temperature"]
                if "target" in hb:
                    self.state.bed_target = hb["target"]
            for i, name in enumerate(self._extruders):
                ex = status.get(name)
                if ex and i < len(self.state.extruder_temps):
                    if "temperature" in ex:
                        self.state.extruder_temps[i] = ex["temperature"]
                    if "target" in ex:
                        self.state.extruder_targets[i] = ex["target"]
            th = status.get("toolhead")
            if th and th.get("extruder") and th["extruder"] in self._extruders:
                self.state.active_extruder = self._extruders.index(th["extruder"])
            fan = status.get("fan")
            if fan and fan.get("speed") is not None:
                self.state.fan_speed = float(fan["speed"])
            trays_changed = self._apply_extra(status)
            cur = self.state.print_state
            trays_now = list(getattr(self.state, "trays", []))
        if trays_changed:
            self._fire_trays_change(trays_now)
        self._fire_state_change()
        if cur == "complete" and self._prev_print_state != "complete":
            self._fire_print_complete()
        self._prev_print_state = cur

    def _fire_state_change(self) -> None:
        if self._on_state_change and self._loop:
            try:
                asyncio.run_coroutine_threadsafe(self._on_state_change(self.state), self._loop)
            except Exception:
                pass

    def _fire_trays_change(self, trays: list) -> None:
        if self._on_ams_change and self._loop:
            try:
                asyncio.run_coroutine_threadsafe(self._on_ams_change(trays), self._loop)
            except Exception:
                pass

    def get_loaded_filaments(self) -> list:
        with self._lock:
            return list(getattr(self.state, "trays", []))

    def _fire_print_complete(self) -> None:
        if self._on_print_complete and self._loop:
            try:
                asyncio.run_coroutine_threadsafe(self._on_print_complete(self.state), self._loop)
            except Exception:
                pass

    def request_status_update(self) -> None:
        self._ws_send("printer.objects.query", {"objects": self._subscribe_objects()})

    # ---- HTTP control (httpx) ----
    def _post(self, path: str, params: dict | None = None) -> bool:
        try:
            r = httpx.post(f"{self._http_base}{path}", params=params, headers=self._headers(), timeout=30)
            r.raise_for_status()
            return True
        except Exception:
            logger.exception("%s %s: POST %s failed", self.label, self._ip, path)
            return False

    def upload_file(self, data: bytes, filename: str) -> bool:
        try:
            files = {"file": (filename, data, "application/octet-stream")}
            r = httpx.post(f"{self._http_base}/server/files/upload",
                           files=files, data={"root": "gcodes"}, headers=self._headers(), timeout=120)
            r.raise_for_status()
            return True
        except Exception:
            logger.exception("%s %s: gcode upload failed (%s)", self.label, self._ip, filename)
            return False

    # ---- file browser (Moonraker file_manager API; root "gcodes") ----
    @staticmethod
    def _gcodes_url_path(file_id: str) -> str:
        return mfiles.gcodes_url_path(file_id)

    def list_files(self, directory: str = "/") -> list[PrinterFile]:
        """One directory of the gcodes root with slicer metadata inline (`extended=true`). Failures raise (httpx errors /
        KeyError): callers must be able to tell "empty" from "couldn't look"."""
        rel, path = mfiles.directory_query(directory)
        r = httpx.get(f"{self._http_base}/server/files/directory", params={"path": path, "extended": "true"},
                      headers=self._headers(), timeout=30)
        r.raise_for_status()
        return mfiles.parse_directory(r.json()["result"], rel)

    def delete_file(self, file_id: str) -> bool:
        try:
            r = httpx.delete(f"{self._http_base}{self._gcodes_url_path(file_id)}", headers=self._headers(), timeout=30)
            r.raise_for_status()
            return True
        except Exception:
            logger.exception("%s %s: delete of %s failed", self.label, self._ip, file_id)
            return False

    def download_file(self, file_id: str, max_bytes: int | None = None) -> bytes | None:
        try:
            chunks: list[bytes] = []
            total = 0
            with httpx.stream("GET", f"{self._http_base}{self._gcodes_url_path(file_id)}",
                              headers=self._headers(), timeout=120) as r:
                r.raise_for_status()
                for chunk in r.iter_bytes():
                    total += len(chunk)
                    if max_bytes is not None and total > max_bytes:
                        raise FileTooLargeError(f"{file_id} is larger than {max_bytes} bytes")
                    chunks.append(chunk)
            return b"".join(chunks)
        except FileTooLargeError:
            raise
        except Exception:
            logger.exception("%s %s: download of %s failed", self.label, self._ip, file_id)
            return None

    def start_print(self, file_name: str, options: StartPrintOptions | None = None) -> bool:
        return self._post("/printer/print/start", params={"filename": file_name})

    def stop_print(self) -> bool:
        return self._post("/printer/print/cancel")

    def pause_print(self) -> bool:
        return self._post("/printer/print/pause")

    def resume_print(self) -> bool:
        return self._post("/printer/print/resume")

    def send_gcode(self, gcode: str) -> bool:
        return self._post("/printer/gcode/script", params={"script": gcode})

    def set_bed_temp(self, celsius: int) -> bool:
        return self.send_gcode(f"M140 S{int(celsius)}")

    def set_fan_speeds(self, model_pct: int, aux_pct: int, box_pct: int) -> bool:
        with self._lock:
            has_fan = self.state.fan_speed is not None
        if not has_fan:
            return False                       # no `fan` object: refuse rather than send an M106 the firmware may ignore
        return self.send_gcode(f"M106 S{round(max(0, min(100, int(model_pct))) * 255 / 100)}")
