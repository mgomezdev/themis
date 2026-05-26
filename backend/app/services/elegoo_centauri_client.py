from __future__ import annotations
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, ClassVar, Optional

import websocket

from .abstract_printer_client import (
    AbstractPrinterClient,
    ConnectionField,
    PrinterCapabilities,
    StartPrintOptions,
)

DEFAULT_PORT = 3030
ACK_TIMEOUT = 10


class _SdcpCmd:
    GET_STATUS = 0
    GET_ATTR = 1
    START_PRINT = 128
    SUSPEND_PRINT = 129
    STOP_PRINT = 130
    RESTORE_PRINT = 131
    EDIT_AXIS_ZERO = 402


@dataclass
class ElegooState:
    connected: bool = False
    print_state: str = "unknown"
    progress: float = 0.0
    remaining_time: int = 0
    layer_num: int = 0
    total_layers: int = 0
    temperatures: dict = field(default_factory=dict)
    current_print: str | None = None
    mainboard_id: str = ""

    @property
    def state(self) -> str:
        return self.print_state

    @property
    def raw_data(self) -> None:
        return None


class ElegooCentauriClient(AbstractPrinterClient):
    printer_type: ClassVar[str] = "elegoo_centauri"

    def __init__(
        self,
        ip_address: str,
        port: int = DEFAULT_PORT,
        camera_url: str = "",
        on_state_change: Callable | None = None,
        on_print_complete: Callable | None = None,
    ) -> None:
        self._ip = ip_address
        self._port = port
        self._camera_url = camera_url
        self._on_state_change = on_state_change
        self._on_print_complete = on_print_complete
        self.state = ElegooState()
        self._ws: websocket.WebSocket | None = None
        self._thread: threading.Thread | None = None
        self._pending_acks: dict[str, threading.Event] = {}
        self._ack_results: dict[str, int] = {}
        self._loop = None

    @classmethod
    def connection_fields(cls) -> list[ConnectionField]:
        return [
            ConnectionField(name="ip_address", label="IP Address", field_type="text", placeholder="192.168.1.x"),
            ConnectionField(name="port", label="Port", field_type="number", default=DEFAULT_PORT, required=False),
            ConnectionField(name="camera_url", label="Camera URL (MJPEG)", field_type="text", required=False,
                            placeholder="http://192.168.1.x:8080/?action=stream",
                            help_text="Optional — leave blank if no camera"),
        ]

    @property
    def connected(self) -> bool:
        return self.state.connected

    def get_capabilities(self) -> PrinterCapabilities:
        return PrinterCapabilities(
            pause_resume=True,
            camera=bool(self._camera_url),
        )

    @property
    def camera_mjpeg_url(self) -> str | None:
        return self._camera_url or None

    @property
    def camera_rtsp_url(self) -> str | None:
        return None

    @property
    def is_idle(self) -> bool:
        return self.state.print_state in ("IDLE", "FINISH")

    @property
    def is_printing(self) -> bool:
        return self.state.print_state == "RUNNING"

    def connect(self, loop=None) -> None:
        self._loop = loop
        url = f"ws://{self._ip}:{self._port}/websocket"
        ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_close=self._on_close,
        )
        self._ws = ws
        self._thread = threading.Thread(target=ws.run_forever, daemon=True)
        self._thread.start()

    def disconnect(self, timeout: int = 0) -> None:
        if self._ws:
            self._ws.close()
        self.state.connected = False

    def check_staleness(self) -> bool:
        return self.state.connected

    def request_status_update(self) -> None:
        self._send(_SdcpCmd.GET_STATUS, {}, wait_ack=False)

    def send_gcode(self, gcode: str) -> bool:
        return False

    @property
    def gcode_supported(self) -> bool:
        return False

    def start_print(self, file_name: str, options: StartPrintOptions | None = None) -> bool:
        return self._send(_SdcpCmd.START_PRINT, {"Filename": file_name})

    def stop_print(self) -> bool:
        return self._send(_SdcpCmd.STOP_PRINT, {})

    def pause_print(self) -> bool:
        return self._send(_SdcpCmd.SUSPEND_PRINT, {})

    def resume_print(self) -> bool:
        return self._send(_SdcpCmd.RESTORE_PRINT, {})

    def home(self) -> bool:
        return self._send(_SdcpCmd.EDIT_AXIS_ZERO, {"Axis": "XYZ"})

    def _send(self, cmd: int, data: dict, wait_ack: bool = True) -> bool:
        if not self._ws:
            return False
        request_id = str(uuid.uuid4())
        payload = {
            "Id": "",
            "Data": {
                "Cmd": cmd,
                "Data": data,
                "RequestID": request_id,
                "MainboardID": self.state.mainboard_id,
                "TimeStamp": int(time.time()),
                "From": 1,
            },
        }
        if wait_ack:
            event = threading.Event()
            self._pending_acks[request_id] = event
        self._ws.send(json.dumps(payload))
        if wait_ack:
            fired = event.wait(timeout=ACK_TIMEOUT)
            result = self._ack_results.pop(request_id, -1)
            self._pending_acks.pop(request_id, None)
            return fired and result == 0
        return True

    def _on_open(self, ws) -> None:
        self.state.connected = True
        self.request_status_update()

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        self.state.connected = False

    def _on_message(self, ws, message: str) -> None:
        try:
            data = json.loads(message)
        except Exception:
            return

        # Printer-pushed status message: {"Status": {...}, ...}
        if "Status" in data:
            d = data.get("Data", {})
            if d.get("MainboardID"):
                self.state.mainboard_id = d["MainboardID"]
            self._update_state(data["Status"])
            return

        # Command response: {"Data": {"Cmd": N, "Data": {...}, "RequestID": "...", ...}}
        d = data.get("Data", {})
        raw_request_id = d.get("RequestID", "")
        # Printer appends \x01 to RequestID in responses — strip it
        request_id = raw_request_id.rstrip("\x01")
        if d.get("MainboardID"):
            self.state.mainboard_id = d["MainboardID"]
        if request_id in self._pending_acks:
            self._ack_results[request_id] = d.get("Result", -1)
            self._pending_acks[request_id].set()
        if d.get("Cmd") == _SdcpCmd.GET_STATUS:
            self._update_state(d.get("Data", {}))

    def _update_state(self, data: dict) -> None:
        prev = self.state.print_state
        state_map = {0: "IDLE", 1: "RUNNING", 2: "PAUSE", 3: "FINISH", 4: "FAILED"}
        if "CurrentStatus" in data:
            raw = data["CurrentStatus"]
            # Printer sends CurrentStatus as a list (e.g. [0]); also handle bare int
            code = raw[0] if isinstance(raw, list) else raw
            self.state.print_state = state_map.get(code, "unknown")
        if "PrintInfo" in data:
            info = data["PrintInfo"]
            # "Status" inside PrintInfo mirrors top-level CurrentStatus
            if "Status" in info and "CurrentStatus" not in data:
                self.state.print_state = state_map.get(info["Status"], "unknown")
            total = int(info.get("TotalTicks") or info.get("TotalLayer") or 0)
            current = int(info.get("CurrentTicks") or info.get("CurrentLayer") or 0)
            self.state.total_layers = total
            self.state.layer_num = current
            self.state.progress = (current / total * 100.0) if total else float(info.get("Progress", 0))
            self.state.current_print = info.get("Filename") or None
            self.state.remaining_time = int(info.get("RemainTime") or info.get("remaining_time") or 0)
        if self._on_state_change and self._loop:
            import asyncio
            asyncio.run_coroutine_threadsafe(
                self._on_state_change(self.state), self._loop
            )
        if self._on_print_complete and self._loop and self.state.print_state == "FINISH" and prev in ("RUNNING", "PAUSE"):
            import asyncio
            asyncio.run_coroutine_threadsafe(
                self._on_print_complete(self.state), self._loop
            )
