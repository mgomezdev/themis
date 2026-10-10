from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import ClassVar

import httpx  # noqa: F401  (the transport lives in services/moonraker; tests patch `<this module>.httpx.*`)

from ...services.abstract_printer_client import ConnectionField, DiscoveredPrinter, PrinterCapabilities
from ...services.moonraker.client import DEFAULT_PORT, MoonrakerClientBase, MoonrakerState, NORM_STATE as _NORM_STATE

logger = logging.getLogger(__name__)

# Vendor-only Klipper object the U1 firmware adds: its per-tool filament setup.
_SUBSCRIBE_OBJECTS = {"print_task_config": None}


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
            out.append({"slot": i, "filament_id": None, "name": "", "type": "", "color": "", "empty": True})
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


@dataclass
class SnapmakerState(MoonrakerState):
    """The U1 is a four-tool printer: its extruder lists start at four entries and it tracks the per-tool filament setup."""
    extruder_temps: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    extruder_targets: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    task_config: dict = field(default_factory=dict)   # print_task_config, merged (notifications carry changed keys only)
    trays: list = field(default_factory=list)         # loaded filaments per tool, derived from task_config


def serialize_snapmaker(state, printer_id: int) -> dict:
    conn = bool(getattr(state, "connected", False) and getattr(state, "klippy_ready", False))
    return {
        "printer_type": "snapmaker_extended",
        "id": printer_id,
        "connected": conn,
        "state": getattr(state, "state", "unknown"),
        "current_print": getattr(state, "current_print", None),
        "progress": getattr(state, "progress", 0.0) * 100.0,  # Klipper display_status.progress is 0..1; the API is 0..100
        "remaining_time": getattr(state, "remaining_time", 0) or 0,
        "layer_num": getattr(state, "layer_num", 0),
        "total_layers": getattr(state, "total_layers", 0),
        "temperatures": getattr(state, "temperatures", {}),
        "fan_model": 0,
        "fan_aux": 0,
        "fan_box": 0,
        "speed_factor": 1.0,
        "klippy_state": "ready" if conn else "disconnected",
        "cover_url": None,
    }


class SnapmakerExtendedClient(MoonrakerClientBase):
    """Moonraker/Klipper client for the Snapmaker U1 Extended firmware: the shared Moonraker transport
    (`services/moonraker/client.py`) plus what is the U1's own — four tools, `print_task_config` filament setup, its camera
    endpoint, tool-mapping at slice time."""
    slice_tool_mapping = True    # filament->tool routing is baked into the 3MF at slice time (the slicing provider rewrites it)
    printer_type: ClassVar[str] = "snapmaker_extended"
    label = "Snapmaker"

    def __init__(self, ip_address: str, port: int | str = DEFAULT_PORT, api_key: str | None = None,
                 on_state_change=None, on_print_complete=None) -> None:
        super().__init__(ip_address, port, api_key, on_state_change, on_print_complete, toolheads=4)

    def _new_state(self) -> SnapmakerState:
        return SnapmakerState()

    def _extra_objects(self) -> dict:
        return dict(_SUBSCRIBE_OBJECTS)

    def _apply_extra(self, status: dict) -> bool:
        ptc = status.get("print_task_config")
        if not ptc:
            return False
        self.state.task_config = {**self.state.task_config, **ptc}
        trays = _trays_from_task_config(self.state.task_config)
        if trays != self.state.trays:
            self.state.trays = trays
            return True
        return False

    @classmethod
    async def discover_host(cls, net, ip: str):
        return await cls._discover_moonraker(net, ip, "Moonraker / Klipper")

    def serialize_state(self, printer_id: int) -> dict:
        return serialize_snapmaker(self.state, printer_id)

    def get_capabilities(self) -> PrinterCapabilities:
        return PrinterCapabilities(
            pause_resume=True, gcode=True, camera=True, temp_control=True,
            axis_jog=True, home_axes=True, nozzle_temp=True, direct_upload=True,
            file_browser=True, file_delete=True, file_download=True,
        )

    @property
    def camera_mjpeg_url(self) -> str | None:
        # Verified on a U1: /webcam/stream is a 404; the MJPEG endpoint is /webcam/stream.mjpg (Moonraker's webcam
        # entry only advertises webrtc + /webcam/snapshot.jpg).
        return f"http://{self._ip}/webcam/stream.mjpg"

    def set_fan_speeds(self, model_pct: int, aux_pct: int, box_pct: int) -> bool:
        return False        # unverified on the U1 firmware
