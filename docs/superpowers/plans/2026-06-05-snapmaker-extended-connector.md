# Snapmaker U1 "Extended" Connector — Implementation Plan (Project 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add the `snapmaker_extended` printer vendor — a Moonraker/Klipper client for the Snapmaker U1 Extended firmware — so the U1 can be added, monitored (WebSocket status), sliced for, and printed to through Themis's existing pipeline.

**Architecture:** One `AbstractPrinterClient` subclass (`SnapmakerExtendedClient`) modeled on the existing Elegoo client (`websocket-client` `WebSocketApp` in a background thread for Moonraker JSON-RPC push status + `httpx` for HTTP control), plus a manager status serializer and a registry entry. No new dependencies, no frontend changes (the wizard/Fleet/Edit are vendor-agnostic).

**Tech Stack:** Python, `websocket-client`, `httpx`, pytest. Backend only.

**Spec:** `docs/superpowers/specs/2026-06-05-snapmaker-extended-connector-design.md`. **Branch:** `snapmaker-u1`.

## Conventions (give to every subagent)
- Backend tests: `cd backend && backend\.venv\Scripts\python.exe -m pytest <path> -v` (python.org venv; `pytest` may not be on PATH).
- `HTTPException(code, "msg")` is positional (not used here, FYI).
- Commit after each task. Don't push.

## Model tuning
**Task 1** (the client + test) is **Sonnet** — it's one substantial file with JSON-RPC parsing and httpx mocking; the code is complete below but placement/test-wiring needs care. **Tasks 2, 3, 4** are **Haiku** (mechanical, complete code). **Task 5** (docs) is **Sonnet** (skill-driven).

## File structure
- **Create** `backend/app/services/snapmaker_client.py` — the client (Task 1).
- **Create** `backend/tests/services/test_snapmaker_client.py` (Task 1).
- **Modify** `backend/app/services/printer_manager.py` — `_serialize_snapmaker` + `_STATUS_SERIALIZERS` (Task 2).
- **Modify** `backend/app/services/printer_client_factory.py` — `REGISTRY` + `_DISPLAY_NAMES` (Task 3).
- **Create** `backend/tests/services/test_snapmaker_registry.py` (Task 3).
- **Create** `scripts/snapmaker_smoke_test.py` (Task 4).
- **Docs:** `docs/agent/printers.md` via themis-docs-sync (Task 5).

---

## Task 1: `SnapmakerExtendedClient`

**Model: Sonnet**

**Files:**
- Create: `backend/app/services/snapmaker_client.py`
- Create: `backend/tests/services/test_snapmaker_client.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_snapmaker_client.py
from unittest.mock import MagicMock, patch
from app.services.snapmaker_client import SnapmakerExtendedClient, SnapmakerState


def _client():
    return SnapmakerExtendedClient(ip_address="192.168.0.119", port=7125)


def test_connection_fields():
    names = [f.name for f in SnapmakerExtendedClient.connection_fields()]
    assert names == ["ip_address", "port", "api_key"]


def test_control_endpoint():
    assert _client().control_endpoint() == ("192.168.0.119", 7125)


def test_is_idle_and_printing_from_print_state():
    c = _client()
    c.state.print_state = "standby"
    assert c.is_idle is True and c.is_printing is False
    c.state.print_state = "printing"
    assert c.is_idle is False and c.is_printing is True
    c.state.print_state = "paused"
    assert c.is_printing is True
    c.state.print_state = "complete"
    assert c.is_idle is True


def test_apply_status_updates_state():
    c = _client()
    c._apply_status({
        "print_stats": {"state": "printing", "filename": "cube.gcode",
                        "print_duration": 120.0, "info": {"current_layer": 5, "total_layer": 100}},
        "display_status": {"progress": 0.25},
        "heater_bed": {"temperature": 60.0, "target": 60.0},
        "extruder": {"temperature": 215.0, "target": 220.0},
        "toolhead": {"extruder": "extruder"},
    })
    assert c.state.print_state == "printing"
    assert c.state.state == "RUNNING"           # normalized
    assert c.state.current_print == "cube.gcode"
    assert c.state.progress == 0.25
    assert c.state.layer_num == 5 and c.state.total_layers == 100
    temps = c.state.temperatures
    assert temps["bed"] == 60.0 and temps["nozzle"] == 215.0
    assert temps["extruders"][0]["temp"] == 215.0


def test_print_complete_fires_once_on_transition():
    c = _client()
    c._fire_print_complete = MagicMock()
    c._apply_status({"print_stats": {"state": "printing"}})
    c._apply_status({"print_stats": {"state": "complete"}})
    c._apply_status({"print_stats": {"state": "complete"}})  # no re-fire
    assert c._fire_print_complete.call_count == 1


def test_http_control_calls():
    c = _client()
    with patch("app.services.snapmaker_client.httpx.post") as post:
        post.return_value = MagicMock(raise_for_status=MagicMock())
        assert c.start_print("cube.gcode") is True
        url, kw = post.call_args[0][0], post.call_args.kwargs
        assert url.endswith("/printer/print/start") and kw["params"]["filename"] == "cube.gcode"

        c.pause_print();  assert post.call_args[0][0].endswith("/printer/print/pause")
        c.resume_print(); assert post.call_args[0][0].endswith("/printer/print/resume")
        c.stop_print();   assert post.call_args[0][0].endswith("/printer/print/cancel")
        c.send_gcode("M104 S200")
        assert post.call_args[0][0].endswith("/printer/gcode/script")
        assert post.call_args.kwargs["params"]["script"] == "M104 S200"


def test_upload_file_posts_multipart():
    c = _client()
    with patch("app.services.snapmaker_client.httpx.post") as post:
        post.return_value = MagicMock(raise_for_status=MagicMock())
        assert c.upload_file(b"G28\n", "cube.gcode") is True
        assert post.call_args[0][0].endswith("/server/files/upload")
        assert "files" in post.call_args.kwargs
```

- [ ] **Step 2: Run the tests — confirm they FAIL**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_snapmaker_client.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.snapmaker_client`.

- [ ] **Step 3: Implement the client**

```python
# backend/app/services/snapmaker_client.py
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, ClassVar

import httpx
import websocket

from .abstract_printer_client import (
    AbstractPrinterClient,
    ConnectionField,
    PrinterCapabilities,
    StartPrintOptions,
)

logger = logging.getLogger(__name__)

DEFAULT_PORT = 7125
_RECONNECT_DELAY = 5.0
_STALE_TIMEOUT = 30.0
_STALE_RECONNECT_COOLDOWN = 20.0

# Klipper print_stats.state -> Themis normalized state string.
# App vocab is IDLE/RUNNING/PAUSE/FINISH/FAILED only (frontend fleet.ts maps FAILED->error).
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
    "display_status": None,
    "heater_bed": None,
    "extruder": None,
    "extruder1": None,
    "extruder2": None,
    "extruder3": None,
    "toolhead": None,
}

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
    raw: dict = field(default_factory=dict)

    @property
    def state(self) -> str:
        return _NORM_STATE.get(self.print_state, self.print_state.upper())

    @property
    def current_print(self) -> str | None:
        return self.filename or None

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
        self.state = SnapmakerState()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._ws: websocket.WebSocketApp | None = None
        self._loop = None
        self._last_message_time = 0.0
        self._last_reconnect_time = 0.0
        self._prev_print_state = "standby"
        self._rpc_id = 0

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
        return PrinterCapabilities(pause_resume=True, gcode=True, camera=True, temp_control=True)

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
        return f"http://{self._ip}/webcam/stream"

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

    def check_staleness(self) -> bool:
        if not self.connected:
            return False
        now = time.time()
        if (now - self._last_message_time > _STALE_TIMEOUT
                and now - self._last_reconnect_time > _STALE_RECONNECT_COOLDOWN):
            self._last_reconnect_time = now
            ws = self._ws
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass
        return self.connected

    # ---- WebSocket JSON-RPC ----
    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

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
        self._last_message_time = time.time()
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
            self._fire_state_change()
        elif method in ("notify_klippy_disconnected", "notify_klippy_shutdown"):
            with self._lock:
                self.state.klippy_ready = False
            self._fire_state_change()
        elif "result" in data:
            result = data["result"]
            if isinstance(result, dict):
                if "klippy_state" in result:
                    with self._lock:
                        self.state.klippy_ready = (result.get("klippy_state") == "ready")
                    self._fire_state_change()
                if "status" in result and isinstance(result["status"], dict):
                    self._apply_status(result["status"])

    def _apply_status(self, status: dict) -> None:
        with self._lock:
            ps = status.get("print_stats")
            if ps:
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
            cur = self.state.print_state
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
```

- [ ] **Step 4: Run the tests — confirm PASS**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_snapmaker_client.py -v`
Expected: PASS (7 passed). Then the full suite `... -m pytest -q` (no regressions).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/snapmaker_client.py backend/tests/services/test_snapmaker_client.py
git commit -m "feat(snapmaker): Moonraker/Klipper client (WebSocket status + httpx control)"
```

---

## Task 2: Manager status serializer

**Model: Haiku**

**Files:**
- Modify: `backend/app/services/printer_manager.py` (add `_serialize_snapmaker`, register it; ~lines 16-67)
- Test: `backend/tests/services/test_snapmaker_serialize.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_snapmaker_serialize.py
from app.services.printer_manager import _STATUS_SERIALIZERS
from app.services.snapmaker_client import SnapmakerState


def test_serialize_snapmaker_shape():
    assert "snapmaker_extended" in _STATUS_SERIALIZERS
    s = SnapmakerState()
    s.connected = True
    s.klippy_ready = True
    s.print_state = "printing"
    s.filename = "cube.gcode"
    s.progress = 0.5
    s.extruder_temps = [210.0, 0.0, 0.0, 0.0]
    s.bed_temp = 60.0
    d = _STATUS_SERIALIZERS["snapmaker_extended"](s, 7)
    assert d["printer_type"] == "snapmaker_extended"
    assert d["id"] == 7
    assert d["connected"] is True
    assert d["state"] == "RUNNING"
    assert d["current_print"] == "cube.gcode"
    assert d["progress"] == 0.5
    assert d["temperatures"]["bed"] == 60.0
    assert d["temperatures"]["nozzle"] == 210.0
```

- [ ] **Step 2: Run — confirm FAIL**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_snapmaker_serialize.py -v`
Expected: FAIL — `snapmaker_extended` not in `_STATUS_SERIALIZERS`.

- [ ] **Step 3: Implement**

In `backend/app/services/printer_manager.py`, add this function immediately after `_serialize_elegoo` (before the `_STATUS_SERIALIZERS` dict):

```python
def _serialize_snapmaker(state, printer_id: int) -> dict:
    conn = bool(getattr(state, "connected", False) and getattr(state, "klippy_ready", True))
    return {
        "printer_type": "snapmaker_extended",
        "id": printer_id,
        "connected": conn,
        "state": getattr(state, "state", "unknown"),
        "current_print": getattr(state, "current_print", None),
        "progress": getattr(state, "progress", 0.0),
        "remaining_time": 0,
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
```

Then add the registry entry to the `_STATUS_SERIALIZERS` dict:

```python
_STATUS_SERIALIZERS: dict[str, Callable] = {
    "bambu": _serialize_bambu,
    "elegoo_centauri": _serialize_elegoo,
    "snapmaker_extended": _serialize_snapmaker,
}
```

- [ ] **Step 4: Run — confirm PASS + full suite**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_snapmaker_serialize.py -v` → PASS.
Then `... -m pytest -q` → no regressions.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/printer_manager.py backend/tests/services/test_snapmaker_serialize.py
git commit -m "feat(snapmaker): normalized status serializer for the Fleet card"
```

---

## Task 3: Registry entry

**Model: Haiku**

**Files:**
- Modify: `backend/app/services/printer_client_factory.py` (`REGISTRY` + `_DISPLAY_NAMES`)
- Test: `backend/tests/services/test_snapmaker_registry.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_snapmaker_registry.py
from app.services.printer_client_factory import (
    get_printer_types_for_ui, create_client_from_config,
)
from app.services.snapmaker_client import SnapmakerExtendedClient


def test_snapmaker_in_printer_types():
    types = {t["printer_type"]: t for t in get_printer_types_for_ui()}
    assert "snapmaker_extended" in types
    assert types["snapmaker_extended"]["display_name"] == "Snapmaker U1 (Extended)"
    names = [f["name"] for f in types["snapmaker_extended"]["connection_fields"]]
    assert names == ["ip_address", "port", "api_key"]


def test_create_snapmaker_client():
    c = create_client_from_config("snapmaker_extended",
                                  {"ip_address": "192.168.0.119", "port": 7125})
    assert isinstance(c, SnapmakerExtendedClient)
    assert c.control_endpoint() == ("192.168.0.119", 7125)
```

- [ ] **Step 2: Run — confirm FAIL**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_snapmaker_registry.py -v`
Expected: FAIL — `snapmaker_extended` not in REGISTRY.

- [ ] **Step 3: Implement**

In `backend/app/services/printer_client_factory.py`, add the two entries:

```python
REGISTRY: dict[str, str] = {
    "bambu": "app.services.bambu_mqtt.BambuMQTTClient",
    "elegoo_centauri": "app.services.elegoo_centauri_client.ElegooCentauriClient",
    "snapmaker_extended": "app.services.snapmaker_client.SnapmakerExtendedClient",
}

_DISPLAY_NAMES: dict[str, str] = {
    "bambu": "Bambu Lab",
    "elegoo_centauri": "Elegoo Centauri",
    "snapmaker_extended": "Snapmaker U1 (Extended)",
}
```

- [ ] **Step 4: Run — confirm PASS + full suite**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_snapmaker_registry.py -v` → PASS.
Then `... -m pytest -q` → no regressions.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/printer_client_factory.py backend/tests/services/test_snapmaker_registry.py
git commit -m "feat(snapmaker): register snapmaker_extended vendor (wizard/fleet/queue wiring)"
```

---

## Task 4: Smoke-test script

**Model: Haiku**

**Files:**
- Create: `scripts/snapmaker_smoke_test.py`

A manual diagnostic (not pytest) that reads `SNAPMAKER_IP` from the repo `.env` and hits Moonraker to confirm connectivity + the live objects, mirroring `scripts/bambu_smoke_test.py`. No secrets are printed.

- [ ] **Step 1: Create the script**

```python
#!/usr/bin/env python3
"""Snapmaker U1 (Extended) Moonraker connectivity smoke test (manual, not pytest).

Reads SNAPMAKER_IP from the repo-root .env (git-ignored) and queries Moonraker:
  - GET /server/info + /printer/info  (Moonraker/Klipper up?)
  - GET /printer/objects/query for print_stats + extruders + bed (live status)
Run:  backend/.venv/Scripts/python.exe scripts/snapmaker_smoke_test.py
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(root: Path) -> None:
    env = root / ".env"
    if not env.exists():
        return
    for raw in env.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    load_env(ROOT)
    ip = os.environ.get("SNAPMAKER_IP")
    port = os.environ.get("SNAPMAKER_PORT", "7125")
    if not ip:
        print("MISSING SNAPMAKER_IP in .env")
        return 2
    import httpx
    base = f"http://{ip}:{port}"
    print(f"Snapmaker smoke test -> {base}")
    try:
        info = httpx.get(f"{base}/printer/info", timeout=8).json()["result"]
        print(f"  OK: Klipper {info.get('state')} | sw {info.get('software_version')} | host {info.get('hostname')}")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL: /printer/info: {exc}")
        return 1
    try:
        q = (f"{base}/printer/objects/query?print_stats&display_status&heater_bed"
             f"&extruder&extruder1&extruder2&extruder3")
        st = httpx.get(q, timeout=8).json()["result"]["status"]
        ps = st.get("print_stats", {})
        print(f"  print_state={ps.get('state')} file={ps.get('filename') or '-'} "
              f"progress={st.get('display_status', {}).get('progress')}")
        print(f"  bed={st.get('heater_bed', {}).get('temperature')}  "
              f"e0={st.get('extruder', {}).get('temperature')}")
        print("  RESULT: PASS")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL: objects/query: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify it parses + runs**

Run: `cd backend && python -c "import ast; ast.parse(open(r'../scripts/snapmaker_smoke_test.py').read())"` (syntax check).
If the printer is online, optionally run `backend\.venv\Scripts\python.exe scripts\snapmaker_smoke_test.py` and confirm it prints `RESULT: PASS`. (Skip the live run if the printer is offline — it's a manual diagnostic.)

- [ ] **Step 3: Commit**

```bash
git add scripts/snapmaker_smoke_test.py
git commit -m "chore(snapmaker): Moonraker connectivity smoke-test script"
```

---

## Task 5: Update agent docs

**Model: Sonnet** (skill-driven)

Run the `themis-docs-sync` skill against this branch's diff. Update `docs/agent/printers.md` with a Snapmaker section: the `snapmaker_extended` vendor (Moonraker/Klipper), WebSocket status (`printer.objects.subscribe`), HTTP control endpoints (upload/start/pause/resume/cancel/gcode), 4 manual filament slots (no auto-sync, manual loaded_filaments), camera snapshot, connection_fields (ip_address/port/api_key). Note the registry entry + serializer. Commit `docs(agent): sync for snapmaker_extended connector`.

---

## Final review + verify against the real printer
After all tasks: `cd backend && backend\.venv\Scripts\python.exe -m pytest -q` (all green). Then restart the backend (the connector loads on startup) and add the printer in the app (Fleet → Add printer → Snapmaker U1 (Extended), IP `192.168.0.119`, port 7125, blank api key). Confirm the Fleet card shows connected + live temps + state, then slice + print a small single-material model (upload + start + progress + completion). Use `scripts/snapmaker_smoke_test.py` for a quick Moonraker check.

## Self-review notes (author)
- **Spec coverage:** connection_fields (T1/T3), WebSocket subscribe + status mapping + complete callback (T1), HTTP control upload/start/pause/resume/cancel/gcode (T1), is_idle/is_printing + state normalization (T1), camera snapshot/stream (T1), control_endpoint (T1), default raw-gcode export (inherited, no override — noted), serializer (T2), registry + display name (T3), smoke test (T4), docs (T5). All spec sections mapped.
- **Type/name consistency:** `printer_type="snapmaker_extended"` used identically in client, serializer key, REGISTRY, `_DISPLAY_NAMES`, tests. `SnapmakerState` fields (`print_state` raw vs `state` normalized property; `temperatures` property) used consistently across T1 (client) and T2 (serializer test). `connection_fields` order `ip_address/port/api_key` matches T1 + T3 tests. `start_print(file_name, options=None)` matches the abstract signature.
- **Haiku-safety:** T2/T3/T4 are mechanical with complete code; T1 is Sonnet (substantial single file). The `_fire_print_complete` monkeypatch in T1's test avoids needing a real event loop.
- **No frontend changes** — the vendor is rendered generically from connection_fields + serializer + loaded_filaments.
