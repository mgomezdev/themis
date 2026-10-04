from __future__ import annotations

import itertools
import json
import logging
import threading
import urllib.parse
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Callable, ClassVar

import httpx
import websocket

from .abstract_printer_client import (
    AbstractPrinterClient,
    ConnectionField,
    Alarm,
    DiscoveredPrinter,
    FileTooLargeError,
    PrinterCapabilities,
    PrinterFile,
    StartPrintOptions,
)

logger = logging.getLogger(__name__)

DEFAULT_PORT = 7125
_RECONNECT_DELAY = 5.0
# Klipper print_stats.state -> Themis normalized state string.
_NORM_STATE = {
    "standby": "IDLE",
    "printing": "RUNNING",
    "paused": "PAUSE",
    "complete": "FINISH",
    "cancelled": "FAILED",
    "error": "FAILED",
}

# Objects we subscribe to / query for live status.
_SUBSCRIBE_OBJECTS = {
    "print_stats": None,
    "webhooks": None,
    "display_status": None,
    "heater_bed": None,
    "extruder": None,
    "extruder1": None,
    "extruder2": None,
    "extruder3": None,
    "toolhead": None,
    "print_task_config": None,
}


def _trays_from_task_config(cfg: dict) -> list[dict]:
    """One loaded-filament dict per tool (T0..T3), from Klipper `print_task_config` (the U1's per-tool filament
    setup: type/vendor/colour and whether a spool is present). Positional on purpose: the queue resolves a job's
    `tool_index` as the list index, so an empty tool stays in the list as a typeless placeholder (type "")."""
    exist = cfg.get("filament_exist") or []
    types = cfg.get("filament_type") or []
    vendors = cfg.get("filament_vendor") or []
    subs = cfg.get("filament_sub_type") or []
    colors = cfg.get("filament_color_rgba") or []
    out: list[dict] = []
    for i in range(4):
        ftype = str(types[i]).strip() if i < len(types) and types[i] else ""
        if not (i < len(exist) and exist[i]) or ftype.upper() in ("", "NONE"):
            out.append({"slot": i, "filament_id": None, "name": "", "type": "", "color": ""})
            continue
        vendor = str(vendors[i]).strip() if i < len(vendors) and vendors[i] else ""
        sub = str(subs[i]).strip() if i < len(subs) and subs[i] else ""
        rgba = str(colors[i]).strip() if i < len(colors) and colors[i] else ""
        out.append({
            "slot": i,
            "filament_id": None,
            "name": " ".join(p for p in ("" if vendor.upper() == "NONE" else vendor, sub or ftype) if p),
            "type": ftype,
            "color": f"#{rgba[:6].upper()}" if len(rgba) >= 6 else "",
        })
    return out


_EXTRUDER_NAMES = ("extruder", "extruder1", "extruder2", "extruder3")
_EXTRUDER_INDEX = {name: i for i, name in enumerate(_EXTRUDER_NAMES)}


@dataclass
class SnapmakerState:
    connected: bool = False
    klippy_ready: bool = False
    print_state: str = "standby"          # raw Klipper print_stats.state
    filename: str | None = None
    progress: float = 0.0
    print_duration: float = 0.0
    layer_num: int = 0
    total_layers: int = 0
    bed_temp: float = 0.0
    bed_target: float = 0.0
    extruder_temps: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    extruder_targets: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    active_extruder: int = 0
    klippy_state: str | None = None      # webhooks.state
    klippy_message: str | None = None    # webhooks.state_message
    print_message: str | None = None     # print_stats.message
    task_config: dict = field(default_factory=dict)   # print_task_config, merged (notifications carry changed keys only)
    trays: list = field(default_factory=list)         # loaded filaments per tool, derived from task_config
    raw: dict = field(default_factory=dict)

    @property
    def state(self) -> str:
        return _NORM_STATE.get(self.print_state, self.print_state.upper())

    @property
    def current_print(self) -> str | None:
        return self.filename or None

    @property
    def remaining_time(self) -> int:
        if self.progress > 0.001 and self.progress < 1.0:
            return int(self.print_duration * (1.0 - self.progress) / self.progress / 60.0)
        return 0

    @property
    def temperatures(self) -> dict:
        i = self.active_extruder if 0 <= self.active_extruder < 4 else 0
        return {
            "nozzle": self.extruder_temps[i],
            "nozzle_target": self.extruder_targets[i],
            "bed": self.bed_temp,
            "bed_target": self.bed_target,
            "extruders": [
                {"index": j, "temp": self.extruder_temps[j], "target": self.extruder_targets[j]}
                for j in range(4)
            ],
        }


class SnapmakerExtendedClient(AbstractPrinterClient):
    """Moonraker/Klipper client for the Snapmaker U1 Extended firmware.

    Status streams over the Moonraker WebSocket (printer.objects.subscribe);
    control goes over Moonraker HTTP. Modeled on ElegooCentauriClient.
    """

    printer_type: ClassVar[str] = "snapmaker_extended"

    def __init__(
        self,
        ip_address: str,
        port: int | str = DEFAULT_PORT,
        api_key: str | None = None,
        on_state_change: Callable | None = None,
        on_print_complete: Callable | None = None,
    ) -> None:
        self._ip = ip_address
        self._port = int(port) if port else DEFAULT_PORT
        self._api_key = (api_key or "").strip() or None
        self._on_state_change = on_state_change
        self._on_print_complete = on_print_complete
        self._on_ams_change = None          # wired by PrinterManager.connect_printer: persists loaded_filaments
        self.state = SnapmakerState()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._ws: websocket.WebSocketApp | None = None
        self._loop = None
        self._prev_print_state = "standby"
        self._rpc_id = itertools.count(1)

    def get_alarms(self) -> list[Alarm]:
        from .alarm_codes import klipper_alarms
        with self._lock:
            s = self.state
            return klipper_alarms({"state": s.klippy_state, "state_message": s.klippy_message},
                                  {"state": s.print_state, "message": s.print_message})

    # ---- discovery (Moonraker web API: GET /server/info, GET /printer/info) ----
    @classmethod
    async def discover_host(cls, net, ip: str):
        """Moonraker answers `GET /server/info` on :7125 with `result.moonraker_version`; with an API key configured
        and the caller untrusted it answers 401/403 — still a Moonraker, flagged as needing a key."""
        status, body = await net.http_get_json(f"http://{ip}:{DEFAULT_PORT}/server/info", 1.0)
        cfg = {"ip_address": ip, "port": DEFAULT_PORT}
        if status in (401, 403):
            return DiscoveredPrinter(printer_type="snapmaker_extended", ip=ip, model="Moonraker / Klipper",
                                     connection_config=cfg, note="Requires an API key")
        result = (body or {}).get("result")
        if status != 200 or not isinstance(result, dict) or "moonraker_version" not in result:
            return None
        _s, info = await net.http_get_json(f"http://{ip}:{DEFAULT_PORT}/printer/info", 1.0)
        hostname = ((info or {}).get("result") or {}).get("hostname")
        return DiscoveredPrinter(printer_type="snapmaker_extended", ip=ip, model="Moonraker / Klipper",
                                 name=hostname, connection_config=cfg)

    # ---- ABC metadata ----
    @classmethod
    def connection_fields(cls) -> list[ConnectionField]:
        return [
            ConnectionField(name="ip_address", label="IP Address", field_type="text",
                            placeholder="192.168.0.x"),
            ConnectionField(name="port", label="Moonraker port", field_type="number",
                            default=DEFAULT_PORT, required=False),
            ConnectionField(name="api_key", label="API key", field_type="password", required=False,
                            help_text="Only if Moonraker requires an API key; leave blank for an open LAN printer."),
        ]

    def get_capabilities(self) -> PrinterCapabilities:
        return PrinterCapabilities(
            pause_resume=True, gcode=True, camera=True, temp_control=True,
            axis_jog=True, home_axes=True, nozzle_temp=True, direct_upload=True,
            file_browser=True, file_delete=True, file_download=True,
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
        # Verified on a U1: /webcam/stream is a 404; the MJPEG endpoint is /webcam/stream.mjpg (Moonraker's webcam
        # entry only advertises webrtc + /webcam/snapshot.jpg).
        return f"http://{self._ip}/webcam/stream.mjpg"

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
        threading.Thread(target=self._run_ws, name=f"snapmaker-{self._ip}", daemon=True).start()

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
            logger.info("Snapmaker %s: opening Moonraker WebSocket %s", self._ip, url)
            ws.run_forever(ping_interval=30, ping_timeout=10)
            self._ws = None
            if not self._stop_event.is_set():
                self._stop_event.wait(_RECONNECT_DELAY)

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
            logger.exception("Snapmaker %s: WebSocket send failed (%s)", self._ip, method)

    def _on_ws_open(self, ws) -> None:
        with self._lock:
            self.state.connected = True
        logger.info("Snapmaker %s: Moonraker WebSocket connected", self._ip)
        self._ws_send("server.info")
        self._ws_send("printer.objects.subscribe", {"objects": _SUBSCRIBE_OBJECTS})
        self._ws_send("printer.objects.query", {"objects": _SUBSCRIBE_OBJECTS})

    def _on_ws_close(self, ws, *_) -> None:
        with self._lock:
            was = self.state.connected
            self.state.connected = False
            self.state.klippy_ready = False
        if was:
            logger.warning("Snapmaker %s: Moonraker WebSocket disconnected", self._ip)
            self._fire_state_change()

    def _on_ws_error(self, ws, error) -> None:
        logger.warning("Snapmaker %s: WebSocket error: %s", self._ip, error)

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
            for i, name in enumerate(_EXTRUDER_NAMES):
                ex = status.get(name)
                if ex:
                    if "temperature" in ex:
                        self.state.extruder_temps[i] = ex["temperature"]
                    if "target" in ex:
                        self.state.extruder_targets[i] = ex["target"]
            th = status.get("toolhead")
            if th and th.get("extruder"):
                self.state.active_extruder = _EXTRUDER_INDEX.get(th["extruder"], 0)
            trays_changed = False
            ptc = status.get("print_task_config")
            if ptc:
                self.state.task_config = {**self.state.task_config, **ptc}
                trays = _trays_from_task_config(self.state.task_config)
                if trays != self.state.trays:
                    self.state.trays, trays_changed = trays, True
            cur = self.state.print_state
            trays_now = list(self.state.trays)
        if trays_changed:
            self._fire_trays_change(trays_now)
        self._fire_state_change()
        if cur == "complete" and self._prev_print_state != "complete":
            self._fire_print_complete()
        self._prev_print_state = cur

    def _fire_state_change(self) -> None:
        if self._on_state_change and self._loop:
            import asyncio
            try:
                asyncio.run_coroutine_threadsafe(self._on_state_change(self.state), self._loop)
            except Exception:
                pass

    def _fire_trays_change(self, trays: list) -> None:
        if self._on_ams_change and self._loop:
            import asyncio
            try:
                asyncio.run_coroutine_threadsafe(self._on_ams_change(trays), self._loop)
            except Exception:
                pass

    def get_loaded_filaments(self) -> list:
        with self._lock:
            return list(self.state.trays)

    def _fire_print_complete(self) -> None:
        if self._on_print_complete and self._loop:
            import asyncio
            try:
                asyncio.run_coroutine_threadsafe(self._on_print_complete(self.state), self._loop)
            except Exception:
                pass

    def request_status_update(self) -> None:
        self._ws_send("printer.objects.query", {"objects": _SUBSCRIBE_OBJECTS})

    # ---- HTTP control (httpx) ----
    def _post(self, path: str, params: dict | None = None) -> bool:
        try:
            r = httpx.post(f"{self._http_base}{path}", params=params, headers=self._headers(), timeout=30)
            r.raise_for_status()
            return True
        except Exception:
            logger.exception("Snapmaker %s: POST %s failed", self._ip, path)
            return False

    def upload_file(self, data: bytes, filename: str) -> bool:
        try:
            files = {"file": (filename, data, "application/octet-stream")}
            r = httpx.post(f"{self._http_base}/server/files/upload",
                           files=files, data={"root": "gcodes"}, headers=self._headers(), timeout=120)
            r.raise_for_status()
            return True
        except Exception:
            logger.exception("Snapmaker %s: gcode upload failed (%s)", self._ip, filename)
            return False

    # ---- file browser (Moonraker file_manager API; root "gcodes") ----
    @staticmethod
    def _gcodes_url_path(file_id: str) -> str:
        """`/server/files/gcodes/<quoted path>` — each segment quoted, `..` and absolute paths refused."""
        segments = [s for s in file_id.split("/") if s]
        if not segments or any(s in (".", "..") for s in segments):
            raise ValueError(f"Invalid file id: {file_id!r}")
        return "/server/files/gcodes/" + "/".join(urllib.parse.quote(s, safe="") for s in segments)

    def list_files(self, directory: str = "/") -> list[PrinterFile]:
        """One directory of the gcodes root with slicer metadata inline (`extended=true`): estimated time,
        filament length/weight, slicer. Directories come first as `is_dir` entries whose id is the path to
        pass back as `directory`."""
        rel = directory.strip("/")
        if any(s in (".", "..") for s in rel.split("/")):
            raise ValueError(f"Invalid directory: {directory!r}")
        path = "gcodes" + (f"/{rel}" if rel else "")
        # Failures raise (httpx errors / KeyError): callers must be able to tell "empty" from "couldn't look".
        r = httpx.get(f"{self._http_base}/server/files/directory", params={"path": path, "extended": "true"},
                      headers=self._headers(), timeout=30)
        r.raise_for_status()
        result = r.json()["result"]
        prefix = f"{rel}/" if rel else ""

        def iso(ts) -> str | None:
            try:
                return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OverflowError, OSError):
                return None

        out = [PrinterFile(id=f"{prefix}{d['dirname']}", name=d["dirname"], size=0, modified_at=iso(d.get("modified")),
                           is_dir=True) for d in result.get("dirs", []) if d.get("dirname")]
        for f in result.get("files", []):
            if not f.get("filename"):
                continue
            meta = {k: v for k, v in {
                "estimated_seconds": f.get("estimated_time"),
                "filament_mm": f.get("filament_total"),
                "filament_grams": f.get("filament_weight_total"),
                "slicer": f.get("slicer"),
            }.items() if v is not None}
            out.append(PrinterFile(id=f"{prefix}{f['filename']}", name=f["filename"], size=int(f.get("size") or 0),
                                   modified_at=iso(f.get("modified")), metadata=meta or None))
        return out

    def delete_file(self, file_id: str) -> bool:
        try:
            r = httpx.delete(f"{self._http_base}{self._gcodes_url_path(file_id)}", headers=self._headers(), timeout=30)
            r.raise_for_status()
            return True
        except Exception:
            logger.exception("Snapmaker %s: delete of %s failed", self._ip, file_id)
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
            logger.exception("Snapmaker %s: download of %s failed", self._ip, file_id)
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

    def remap_sliceable_3mf(self, sliceable_3mf, *, tool_index=None, filament_map=None) -> None:
        from .snapmaker.remap import remap_3mf
        remap_3mf(sliceable_3mf, tool_index=tool_index, filament_map=filament_map)
