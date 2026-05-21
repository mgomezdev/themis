from __future__ import annotations
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, ClassVar, Optional

import paho.mqtt.client as mqtt

from .abstract_printer_client import (
    AbstractPrinterClient,
    ConnectionField,
    PrinterCapabilities,
    StartPrintOptions,
)

STALE_TIMEOUT = 60
STALE_RECONNECT_COOLDOWN = 30
MQTT_PORT = 8883


@dataclass
class PrinterState:
    connected: bool = False
    state: str = "unknown"
    stg_cur: int = -1
    current_print: str | None = None
    progress: float = 0.0
    remaining_time: int = 0
    layer_num: int = 0
    total_layers: int = 0
    temperatures: dict = field(default_factory=dict)
    raw_data: dict = field(default_factory=dict)
    hms_errors: list = field(default_factory=list)
    model: str | None = None
    firmware: str | None = None


class BambuMQTTClient(AbstractPrinterClient):
    printer_type: ClassVar[str] = "bambu"

    def __init__(
        self,
        ip_address: str,
        serial_number: str,
        access_code: str,
        on_state_change: Callable | None = None,
        on_print_start: Callable | None = None,
        on_print_complete: Callable | None = None,
        on_ams_change: Callable | None = None,
        on_layer_change: Callable | None = None,
    ) -> None:
        self._ip = ip_address
        self._serial_number = serial_number
        self._access_code = access_code
        self._on_state_change = on_state_change
        self._on_print_start = on_print_start
        self._on_print_complete = on_print_complete
        self._on_ams_change = on_ams_change
        self._on_layer_change = on_layer_change
        self.state = PrinterState()
        self._client: mqtt.Client | None = None
        self._last_message_time: float = 0.0
        self._last_reconnect_time: float = 0.0
        self._loop = None

    @classmethod
    def connection_fields(cls) -> list[ConnectionField]:
        return [
            ConnectionField(name="ip_address", label="IP Address", field_type="text", placeholder="192.168.1.x"),
            ConnectionField(name="serial_number", label="Serial Number", field_type="text", placeholder="01P00A..."),
            ConnectionField(name="access_code", label="Access Code", field_type="password"),
        ]

    @property
    def connected(self) -> bool:
        return self.state.connected

    def connect(self, loop=None) -> None:
        self._loop = loop
        client = mqtt.Client()
        client.username_pw_set("bblp", self._access_code)
        client.tls_set(cert_reqs=False)
        client.tls_insecure_set(True)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        self._client = client
        client.connect_async(self._ip, MQTT_PORT)
        client.loop_start()

    def disconnect(self, timeout: int = 0) -> None:
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
        self.state.connected = False

    def check_staleness(self) -> bool:
        if not self.state.connected:
            return False
        now = time.time()
        if (now - self._last_message_time > STALE_TIMEOUT and
                now - self._last_reconnect_time > STALE_RECONNECT_COOLDOWN):
            self._last_reconnect_time = now
            if self._client:
                try:
                    self._client.socket().close()
                except Exception:
                    pass
        return self.state.connected

    def get_capabilities(self) -> PrinterCapabilities:
        return PrinterCapabilities(
            ams=True,
            pause_resume=True,
            chamber_light=True,
            layer_inspect=True,
            timelapse=True,
            gcode=True,
            camera=True,
            bed_levelling=True,
            vibration_cali=True,
        )

    @property
    def is_idle(self) -> bool:
        if self.state.state == "IDLE":
            return True
        if self.state.state == "FINISH" and self.state.stg_cur == 255:
            return True
        return False

    @property
    def is_printing(self) -> bool:
        return self.state.state in ("RUNNING", "PAUSE")

    def request_status_update(self) -> None:
        self._publish({"pushing": {"command": "pushall", "version": 1, "push_target": 1}})

    def send_gcode(self, gcode: str) -> bool:
        return self._publish({"print": {"command": "gcode_line", "param": f"{gcode}\n", "sequence_id": "0"}})

    def start_print(self, file_name: str, options: StartPrintOptions | None = None) -> bool:
        opts = options or StartPrintOptions()
        payload: dict = {
            "print": {
                "command": "project_file",
                "param": f"Metadata/plate_{opts.plate_id}.gcode",
                "subtask_name": file_name,
                "url": f"ftp://{file_name}",
                "bed_type": "auto",
                "bed_levelling": opts.bed_levelling,
                "flow_cali": opts.flow_cali,
                "vibration_cali": opts.vibration_cali,
                "layer_inspect": opts.layer_inspect,
                "timelapse": opts.timelapse,
                "use_ams": opts.use_ams,
            }
        }
        if opts.ams_mapping is not None:
            payload["print"]["ams_mapping"] = opts.ams_mapping
        return self._publish(payload)

    def stop_print(self) -> bool:
        return self._publish({"print": {"command": "stop", "sequence_id": "0"}})

    def pause_print(self) -> bool:
        return self._publish({"print": {"command": "pause", "sequence_id": "0"}})

    def resume_print(self) -> bool:
        return self._publish({"print": {"command": "resume", "sequence_id": "0"}})

    def set_chamber_light(self, on: bool) -> bool:
        mode = "on" if on else "off"
        self._publish({"system": {"command": "ledctrl", "led_node": "chamber_light", "led_mode": mode}})
        self._publish({"system": {"command": "ledctrl", "led_node": "chamber_light2", "led_mode": mode}})
        return True

    def _publish(self, payload: dict) -> bool:
        if not self._client:
            return False
        self._client.publish(
            f"device/{self._serial_number}/request",
            json.dumps(payload),
            qos=1,
        )
        return True

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc == 0:
            self.state.connected = True
            client.subscribe(f"device/{self._serial_number}/report", qos=1)
            self.request_status_update()
        else:
            self.state.connected = False

    def _on_disconnect(self, client, userdata, rc) -> None:
        self.state.connected = False

    def _on_message(self, client, userdata, msg) -> None:
        self._last_message_time = time.time()
        try:
            data = json.loads(msg.payload)
        except Exception:
            return
        self._handle_message(data)

    def _handle_message(self, data: dict) -> None:
        if "print" not in data:
            return
        p = data["print"]
        prev_state = self.state.state
        if "gcode_state" in p:
            raw = p["gcode_state"]
            self.state.state = {
                "IDLE": "IDLE",
                "RUNNING": "RUNNING",
                "PAUSE": "PAUSE",
                "FINISH": "FINISH",
                "FAILED": "FAILED",
            }.get(raw, "unknown")
        if "stg_cur" in p:
            self.state.stg_cur = p["stg_cur"]
        if "subtask_name" in p:
            self.state.current_print = p["subtask_name"]
        if "mc_percent" in p:
            self.state.progress = float(p["mc_percent"])
        if "mc_remaining_time" in p:
            self.state.remaining_time = int(p["mc_remaining_time"])
        if "layer_num" in p:
            self.state.layer_num = int(p["layer_num"])
        if "total_layer_num" in p:
            self.state.total_layers = int(p["total_layer_num"])
        temps: dict = {}
        if "nozzle_temper" in p:
            temps["nozzle"] = float(p["nozzle_temper"])
        if "bed_temper" in p:
            temps["bed"] = float(p["bed_temper"])
        if "chamber_temper" in p:
            temps["chamber"] = float(p["chamber_temper"])
        if temps:
            self.state.temperatures = temps
        self.state.raw_data = data
        if self._on_state_change and self._loop:
            import asyncio
            asyncio.run_coroutine_threadsafe(
                self._on_state_change(self.state), self._loop
            )
        if self._on_print_complete and self._loop and self.state.state == "FINISH" and prev_state in ("RUNNING", "PAUSE"):
            import asyncio
            asyncio.run_coroutine_threadsafe(
                self._on_print_complete(self.state), self._loop
            )
