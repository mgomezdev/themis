from __future__ import annotations
import ftplib
import io
import json
import logging
import socket
import ssl
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

logger = logging.getLogger(__name__)

STALE_TIMEOUT = 60
STALE_RECONNECT_COOLDOWN = 30
MQTT_PORT = 8883
FTPS_PORT = 990  # Bambu LAN file transfer is implicit FTPS


def _as_bool(v) -> bool:
    """Coerce a config value (arrives as a string from the frontend) to bool."""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


class _ImplicitFTP_TLS(ftplib.FTP_TLS):
    """ftplib only does *explicit* FTPS; Bambu printers speak *implicit* FTPS on
    990 (the control socket is TLS from the first byte). Wrap the socket in TLS as
    soon as it's set."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value, server_hostname=None)
        self._sock = value


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
    fan_model: int = 0
    fan_aux: int = 0
    fan_box: int = 0
    # AMS: parsed loaded-filament trays (see _parse_ams) + the active global tray id.
    ams_trays: list = field(default_factory=list)
    ams_tray_now: int | None = None


class BambuMQTTClient(AbstractPrinterClient):
    printer_type: ClassVar[str] = "bambu"

    def orca_export_args(self, file_base: str) -> list[str]:
        # Bambu printers ingest the sliced .gcode.3mf (not raw gcode); name it
        # after the job so the printer's file list is meaningful.
        return ["--export-3mf", f"{file_base}.gcode.3mf"]

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
        bed_leveling: bool | str = True,
        flow_cali: bool | str = False,
        timelapse: bool | str = False,
        use_ams: bool | str = True,
    ) -> None:
        self._ip = ip_address
        self._serial_number = serial_number
        self._access_code = access_code
        self._on_state_change = on_state_change
        self._on_print_start = on_print_start
        self._on_print_complete = on_print_complete
        self._on_ams_change = on_ams_change
        self._on_layer_change = on_layer_change
        # Per-printer print defaults (start_print uses them unless StartPrintOptions overrides).
        self._bed_leveling = _as_bool(bed_leveling)
        self._flow_cali = _as_bool(flow_cali)
        self._timelapse = _as_bool(timelapse)
        self._use_ams = _as_bool(use_ams)
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
            ConnectionField(name="use_ams", label="Use AMS", field_type="number", default=1, required=False,
                            help_text="1 = print from the AMS (auto-detected filaments), 0 = external spool."),
            ConnectionField(name="bed_leveling", label="Auto bed leveling", field_type="number", default=1, required=False,
                            help_text="1 = run bed leveling before each print, 0 = skip."),
            ConnectionField(name="flow_cali", label="Flow calibration", field_type="number", default=0, required=False,
                            help_text="1 = run dynamic flow calibration before printing, 0 = off."),
            ConnectionField(name="timelapse", label="Timelapse", field_type="number", default=0, required=False,
                            help_text="1 = record a timelapse, 0 = off."),
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
        client.enable_logger(logger)  # route paho's socket/handshake errors into our log
        self._client = client
        logger.info("Bambu %s: opening MQTT connection on %d", self._ip, MQTT_PORT)
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

    @property
    def file_upload_supported(self) -> bool:
        return True

    def upload_file(self, data: bytes, filename: str) -> bool:
        """Upload the sliced .gcode.3mf to the printer over implicit FTPS (port 990,
        credentials bblp / access_code, self-signed TLS)."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ftp = None
        try:
            ftp = _ImplicitFTP_TLS(context=ctx)
            ftp.connect(self._ip, FTPS_PORT, timeout=30)
            ftp.login("bblp", self._access_code)
            ftp.prot_p()  # encrypt the data channel too
            ftp.storbinary(f"STOR {filename}", io.BytesIO(data))
            ftp.quit()
            return True
        except Exception:
            logger.exception("FTPS upload to %s:%d failed (%s, %d bytes)",
                             self._ip, FTPS_PORT, filename, len(data))
            if ftp is not None:
                try:
                    ftp.close()
                except Exception:
                    pass
            return False

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
    def camera_rtsp_url(self) -> str | None:
        return f"rtsps://bblp:{self._access_code}@{self._ip}:322/streaming/live/1"

    @property
    def camera_mjpeg_url(self) -> str | None:
        return None

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

    @staticmethod
    def _parse_ams(p: dict) -> list[dict]:
        """Flatten the Bambu AMS report into loaded-filament dicts (one per non-empty
        tray) compatible with the queue engine's filament matcher. Global tray id =
        unit*4 + tray (external/virtual spool = 254). Color is 8-hex RGBA → #RRGGBB."""
        out: list[dict] = []

        def add(global_id: int, unit_id: int | None, tray: dict) -> None:
            ttype = (tray.get("tray_type") or "").strip()
            if not ttype:
                return  # empty slot
            color8 = (tray.get("tray_color") or "").strip()
            color = f"#{color8[:6]}" if len(color8) >= 6 else "#888888"
            out.append({
                "slot": global_id,
                "ams_tray_id": global_id,
                "ams_unit": unit_id,
                "filament_id": tray.get("tray_info_idx") or None,
                "name": (tray.get("tray_sub_brands") or ttype).strip(),
                "type": ttype,
                "color": color,
            })

        ams = p.get("ams") or {}
        for unit in ams.get("ams", []) or []:
            try:
                unit_id = int(unit.get("id", 0))
            except (TypeError, ValueError):
                unit_id = 0
            for tray in unit.get("tray", []) or []:
                try:
                    tray_id = int(tray.get("id", 0))
                except (TypeError, ValueError):
                    continue
                add(unit_id * 4 + tray_id, unit_id, tray)

        vt = p.get("vt_tray")  # external spool holder
        if isinstance(vt, dict):
            add(254, None, vt)
        return out

    def get_loaded_filaments(self) -> list:
        return list(self.state.ams_trays)

    def start_print(self, file_name: str, options: StartPrintOptions | None = None) -> bool:
        opts = options or StartPrintOptions()
        payload: dict = {
            "print": {
                "command": "project_file",
                "param": opts.gcode_path or f"Metadata/plate_{opts.plate_id}.gcode",
                "subtask_name": file_name,
                "url": f"ftp://{file_name}",
                "bed_type": "auto",
                # Print flags are per-printer config (set via connection fields);
                # the job only supplies the file, plate, and AMS mapping.
                "bed_levelling": self._bed_leveling,
                "flow_cali": self._flow_cali,
                "vibration_cali": opts.vibration_cali,
                "layer_inspect": opts.layer_inspect,
                "timelapse": self._timelapse,
                "use_ams": self._use_ams,
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
            logger.info("Bambu %s: MQTT connected", self._ip)
            client.subscribe(f"device/{self._serial_number}/report", qos=1)
            self.request_status_update()
        else:
            self.state.connected = False
            try:
                reason = mqtt.connack_string(rc)
            except Exception:
                reason = str(rc)
            logger.warning(
                "Bambu %s: MQTT connection refused (rc=%s: %s) — check the access code/serial, "
                "or the printer's single LAN connection may be held by another app (Bambu Studio/Handy).",
                self._ip, rc, reason,
            )

    def _on_disconnect(self, client, userdata, rc) -> None:
        self.state.connected = False
        if rc != 0:
            logger.warning("Bambu %s: MQTT disconnected unexpectedly (rc=%s)", self._ip, rc)

    def control_endpoint(self) -> tuple[str, int]:
        return (self._ip, MQTT_PORT)

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
        # Fans report as 0–15 gears; scale to a 0–100 percentage.
        if "cooling_fan_speed" in p:
            self.state.fan_model = round(int(p["cooling_fan_speed"]) / 15 * 100)
        if "big_fan1_speed" in p:
            self.state.fan_aux = round(int(p["big_fan1_speed"]) / 15 * 100)
        if "big_fan2_speed" in p:
            self.state.fan_box = round(int(p["big_fan2_speed"]) / 15 * 100)

        # AMS: parse trays into loaded-filament dicts and notify on change.
        ams_changed = False
        if "ams" in p or "vt_tray" in p:
            new_trays = self._parse_ams(p)
            if new_trays != self.state.ams_trays:
                self.state.ams_trays = new_trays
                ams_changed = True
            tray_now = (p.get("ams") or {}).get("tray_now")
            if tray_now not in (None, "", "255"):
                try:
                    self.state.ams_tray_now = int(tray_now)
                except (TypeError, ValueError):
                    pass

        self.state.raw_data = data
        if ams_changed and self._on_ams_change and self._loop:
            import asyncio
            asyncio.run_coroutine_threadsafe(
                self._on_ams_change(self.state.ams_trays), self._loop
            )
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
