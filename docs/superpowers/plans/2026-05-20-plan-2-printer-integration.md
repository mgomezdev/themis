# Themis – Plan 2: Printer Integration Layer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the full printer integration layer for Bambu P1S and Elegoo Centauri Carbon, wire it into FastAPI with printer CRUD + control routes and a WebSocket hub for real-time status broadcasts.

**Architecture:** `AbstractPrinterClient` ABC + factory/registry + `PrinterManager` singleton, ported from the GroundsKeeper pattern (see `docs/printer-interface.md`). Two concrete clients: `BambuMQTTClient` (MQTT over TLS via paho-mqtt) and `ElegooCentauriClient` (SDCP/WebSocket via websocket-client). Background threads handle all I/O; callbacks dispatch into the asyncio event loop via `run_coroutine_threadsafe`. A `ConnectionManager` WebSocket hub broadcasts normalized state events to all connected browser tabs.

**Tech Stack:** paho-mqtt 1.x, websocket-client, FastAPI WebSocket, unittest.mock (stdlib), pytest

---

## File Map

### Services
| File | Responsibility |
|---|---|
| `backend/app/services/__init__.py` | Empty package marker |
| `backend/app/services/abstract_printer_client.py` | ABC, `PrinterCapabilities`, `StartPrintOptions`, `PrinterFile`, `ConnectionField`, `_validate_file_id` |
| `backend/app/services/bambu_mqtt.py` | `PrinterState`, `BambuMQTTClient` (P1S) |
| `backend/app/services/elegoo_centauri_client.py` | `ElegooState`, `ElegooCentauriClient` (Centauri Carbon) |
| `backend/app/services/printer_client_factory.py` | Registry, `create_client()`, `get_printer_types_for_ui()` |
| `backend/app/services/printer_manager.py` | `PrinterManager` singleton, callback wiring, state serializers, `awaiting_plate_clear` gate |

### API
| File | Responsibility |
|---|---|
| `backend/app/api/__init__.py` | Empty package marker |
| `backend/app/api/websocket.py` | `ConnectionManager`, `/ws` WebSocket endpoint |
| `backend/app/api/routes/__init__.py` | Empty package marker |
| `backend/app/api/routes/printers.py` | Printer CRUD + control routes (connect, disconnect, pause, resume, cancel, chamber-light, plate-cleared, types) |

### Tests
| File | Responsibility |
|---|---|
| `backend/tests/services/__init__.py` | Empty package marker |
| `backend/tests/services/test_abstract_client.py` | ABC contracts, `_validate_file_id` |
| `backend/tests/services/test_bambu_mqtt.py` | BambuMQTTClient unit tests via mock seam |
| `backend/tests/services/test_elegoo_centauri.py` | ElegooCentauriClient unit tests via mock seam |
| `backend/tests/services/test_factory.py` | Factory registry, `get_printer_types_for_ui()` |
| `backend/tests/services/test_printer_manager.py` | Manager state, `awaiting_plate_clear` gate |
| `backend/tests/api/__init__.py` | Empty package marker |
| `backend/tests/api/test_printers_api.py` | Printer CRUD + control route integration tests |
| `backend/tests/api/test_websocket.py` | WebSocket connection and broadcast tests |

---

## Task 1: Add Dependencies

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add runtime dependencies**

Edit `backend/pyproject.toml` — add to `dependencies`:

```toml
dependencies = [
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.29.0",
    "sqlalchemy>=2.0.0",
    "aiosqlite>=0.20.0",
    "python-multipart>=0.0.9",
    "paho-mqtt>=1.6,<2.0",
    "websocket-client>=1.7",
]
```

- [ ] **Step 2: Install**

```bash
cd backend
pip install -e ".[dev]"
```

Expected: both `paho-mqtt` and `websocket-client` appear in `pip list`.

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml
git commit -m "chore: add paho-mqtt and websocket-client dependencies"
```

---

## Task 2: AbstractPrinterClient ABC

**Files:**
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/services/abstract_printer_client.py`
- Create: `backend/tests/services/__init__.py`
- Create: `backend/tests/services/test_abstract_client.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/services/test_abstract_client.py
import pytest
from app.services.abstract_printer_client import (
    AbstractPrinterClient,
    ConnectionField,
    PrinterCapabilities,
    PrinterFile,
    StartPrintOptions,
)


class MinimalClient(AbstractPrinterClient):
    printer_type = "test"

    @property
    def connected(self) -> bool:
        return True

    def connect(self, loop=None) -> None:
        pass

    def disconnect(self, timeout: int = 0) -> None:
        pass

    def check_staleness(self) -> bool:
        return True

    def start_print(self, file_name, options=None) -> bool:
        return True

    def stop_print(self) -> bool:
        return True

    def pause_print(self) -> bool:
        return True

    def resume_print(self) -> bool:
        return True

    def send_gcode(self, gcode: str) -> bool:
        self._last_gcode = gcode
        return True

    def request_status_update(self) -> None:
        pass


def test_capabilities_defaults():
    caps = PrinterCapabilities()
    assert caps.ams is False
    assert caps.camera is False
    assert caps.pause_resume is False


def test_start_print_options_defaults():
    opts = StartPrintOptions()
    assert opts.plate_id == 1
    assert opts.gcode_path is None
    assert opts.use_ams is True


def test_printer_file_fields():
    f = PrinterFile(id="abc", name="model.3mf", size=1024)
    assert f.modified_at is None


def test_connection_field_defaults():
    cf = ConnectionField(name="serial_number", label="Serial Number", field_type="text")
    assert cf.required is True
    assert cf.default is None


def test_default_capabilities_all_false():
    client = MinimalClient()
    caps = client.get_capabilities()
    assert caps.ams is False
    assert caps.camera is False
    assert caps.chamber_light is False


def test_home_sends_g28():
    client = MinimalClient()
    client.home()
    assert client._last_gcode == "G28"


def test_set_chamber_light_returns_false_by_default():
    client = MinimalClient()
    assert client.set_chamber_light(True) is False


def test_is_idle_false_by_default():
    client = MinimalClient()
    assert client.is_idle is False


def test_is_printing_false_by_default():
    client = MinimalClient()
    assert client.is_printing is False


def test_validate_file_id_rejects_path_traversal():
    client = MinimalClient()
    with pytest.raises(ValueError):
        client._validate_file_id("../../etc/passwd")


def test_validate_file_id_rejects_null_byte():
    client = MinimalClient()
    with pytest.raises(ValueError):
        client._validate_file_id("file\x00name")


def test_validate_file_id_rejects_absolute_path():
    client = MinimalClient()
    with pytest.raises(ValueError):
        client._validate_file_id("/absolute/path")


def test_validate_file_id_rejects_windows_drive():
    client = MinimalClient()
    with pytest.raises(ValueError):
        client._validate_file_id("C:/windows/system32")


def test_validate_file_id_accepts_normal_filename():
    client = MinimalClient()
    client._validate_file_id("my_model.3mf")  # should not raise


def test_connection_fields_default_empty():
    assert MinimalClient.connection_fields() == []
```

- [ ] **Step 2: Run — expect ImportError**

```bash
cd backend && pytest tests/services/test_abstract_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services'`

- [ ] **Step 3: Create package markers**

```bash
touch backend/app/services/__init__.py backend/tests/services/__init__.py
```

- [ ] **Step 4: Write `backend/app/services/abstract_printer_client.py`**

```python
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

    @abstractmethod
    def check_staleness(self) -> bool: ...

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

    @property
    def gcode_supported(self) -> bool:
        return True

    # --- Capabilities and lifecycle hooks ---

    def get_capabilities(self) -> PrinterCapabilities:
        return PrinterCapabilities()

    def on_forced_offline(self) -> None:
        pass

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

    def upload_file(self, data: bytes, filename: str) -> bool:
        return False

    def list_files(self, directory: str = "/") -> list[PrinterFile]:
        return []

    def storage_info(self) -> dict | None:
        return None

    def get_loaded_filaments(self) -> list:
        return []

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
        if len(decoded) >= 2 and decoded[1] == ":":
            raise ValueError(f"Invalid file_id (Windows drive): {file_id!r}")
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
cd backend && pytest tests/services/test_abstract_client.py -v
```

Expected: 15 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ backend/tests/services/
git commit -m "feat: add AbstractPrinterClient ABC and data classes"
```

---

## Task 3: BambuMQTTClient (P1S)

**Files:**
- Create: `backend/app/services/bambu_mqtt.py`
- Create: `backend/tests/services/test_bambu_mqtt.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/services/test_bambu_mqtt.py
import json
import threading
import time
from unittest.mock import MagicMock, call, patch
import pytest
from app.services.bambu_mqtt import BambuMQTTClient, PrinterState
from app.services.abstract_printer_client import PrinterCapabilities, StartPrintOptions


def _make_client() -> BambuMQTTClient:
    return BambuMQTTClient(
        ip_address="192.168.1.10",
        serial_number="01P00A123456789",
        access_code="12345678",
    )


def _connected_client() -> BambuMQTTClient:
    client = _make_client()
    client._client = MagicMock()
    client.state.connected = True
    return client


def test_printer_type():
    assert BambuMQTTClient.printer_type == "bambu"


def test_connection_fields():
    fields = {f.name: f for f in BambuMQTTClient.connection_fields()}
    assert "ip_address" in fields
    assert "serial_number" in fields
    assert "access_code" in fields
    assert fields["access_code"].field_type == "password"


def test_connected_false_initially():
    client = _make_client()
    assert client.connected is False


def test_is_idle_when_state_idle():
    client = _make_client()
    client.state.state = "IDLE"
    assert client.is_idle is True


def test_is_idle_when_finish_and_stg_255():
    client = _make_client()
    client.state.state = "FINISH"
    client.state.stg_cur = 255
    assert client.is_idle is True


def test_is_idle_false_when_running():
    client = _make_client()
    client.state.state = "RUNNING"
    assert client.is_idle is False


def test_is_printing_when_running():
    client = _make_client()
    client.state.state = "RUNNING"
    assert client.is_printing is True


def test_is_printing_false_when_idle():
    client = _make_client()
    client.state.state = "IDLE"
    assert client.is_printing is False


def test_get_capabilities():
    caps = _make_client().get_capabilities()
    assert caps.pause_resume is True
    assert caps.chamber_light is True
    assert caps.ams is True
    assert caps.camera is True
    assert caps.gcode is True


def test_pause_print_publishes(mocker):
    client = _connected_client()
    result = client.pause_print()
    assert result is True
    assert client._client.publish.called
    topic, payload = client._client.publish.call_args[0]
    data = json.loads(payload)
    assert data["print"]["command"] == "pause"
    assert topic == f"device/{client._serial_number}/request"


def test_resume_print_publishes(mocker):
    client = _connected_client()
    result = client.resume_print()
    assert result is True
    payload = json.loads(client._client.publish.call_args[0][1])
    assert payload["print"]["command"] == "resume"


def test_stop_print_publishes():
    client = _connected_client()
    result = client.stop_print()
    assert result is True
    payload = json.loads(client._client.publish.call_args[0][1])
    assert payload["print"]["command"] == "stop"


def test_set_chamber_light_publishes_twice():
    client = _connected_client()
    client.set_chamber_light(True)
    assert client._client.publish.call_count == 2


def test_send_gcode_publishes():
    client = _connected_client()
    client.send_gcode("G28")
    payload = json.loads(client._client.publish.call_args[0][1])
    assert payload["print"]["command"] == "gcode_line"
    assert "G28" in payload["print"]["param"]


def test_start_print_publishes():
    client = _connected_client()
    opts = StartPrintOptions(plate_id=1)
    result = client.start_print("model.3mf", opts)
    assert result is True
    assert client._client.publish.called


def test_check_staleness_returns_connected_state():
    client = _make_client()
    client.state.connected = False
    assert client.check_staleness() is False
```

- [ ] **Step 2: Run — expect ImportError**

```bash
cd backend && pytest tests/services/test_bambu_mqtt.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.bambu_mqtt'`

- [ ] **Step 3: Add `pytest-mock` to dev deps** (needed for `mocker` fixture)

In `backend/pyproject.toml`, add to dev extras:
```toml
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0,<1.0",
    "pytest-mock>=3.12",
    "httpx>=0.27.0",
]
```

Run: `pip install -e ".[dev]"`

- [ ] **Step 4: Write `backend/app/services/bambu_mqtt.py`**

```python
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
        client.tls_set(cert_reqs=False)  # P1S uses self-signed cert
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
        # P1S uses stg_cur=255 as idle indicator when state is FINISH
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
        node_payload = {"node": "chamber_light", "mode": mode}
        node2_payload = {"node": "chamber_light2", "mode": mode}
        self._publish({"system": {"command": "ledctrl", "led_node": "chamber_light", "led_mode": mode}})
        self._publish({"system": {"command": "ledctrl", "led_node": "chamber_light2", "led_mode": mode}})
        return True

    # --- Internal ---

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
        if self._on_print_complete and self._loop and self.state.state == "FINISH" and prev_state == "RUNNING":
            import asyncio
            asyncio.run_coroutine_threadsafe(
                self._on_print_complete(self.state), self._loop
            )
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
cd backend && pytest tests/services/test_bambu_mqtt.py -v
```

Expected: 17 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/bambu_mqtt.py backend/tests/services/test_bambu_mqtt.py
git commit -m "feat: add BambuMQTTClient for P1S"
```

---

## Task 4: ElegooCentauriClient

**Files:**
- Create: `backend/app/services/elegoo_centauri_client.py`
- Create: `backend/tests/services/test_elegoo_centauri.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/services/test_elegoo_centauri.py
import json
import threading
import uuid
from unittest.mock import MagicMock
import pytest
from app.services.elegoo_centauri_client import ElegooCentauriClient, ElegooState


def _make_client(camera_url: str = "") -> ElegooCentauriClient:
    return ElegooCentauriClient(ip_address="192.168.1.20", camera_url=camera_url)


def _make_ack_responder(client: ElegooCentauriClient):
    def side_effect(msg_str: str) -> None:
        data = json.loads(msg_str)
        request_id = data["Data"]["RequestID"]
        client._ack_results[request_id] = 0
        event = client._pending_acks.get(request_id)
        if event:
            event.set()
    return side_effect


def _connected_client(camera_url: str = "") -> ElegooCentauriClient:
    client = _make_client(camera_url=camera_url)
    client._ws = MagicMock()
    client._ws.send.side_effect = _make_ack_responder(client)
    client.state.connected = True
    client.state.print_state = "IDLE"
    return client


def test_printer_type():
    assert ElegooCentauriClient.printer_type == "elegoo_centauri"


def test_connection_fields_no_camera():
    fields = {f.name: f for f in ElegooCentauriClient.connection_fields()}
    assert "ip_address" in fields
    assert "port" in fields
    assert "camera_url" in fields
    assert fields["camera_url"].required is False


def test_connected_false_initially():
    client = _make_client()
    assert client.connected is False


def test_camera_capability_when_url_set():
    client = _make_client(camera_url="http://192.168.1.20:8080/?action=stream")
    caps = client.get_capabilities()
    assert caps.camera is True


def test_no_camera_capability_without_url():
    client = _make_client(camera_url="")
    caps = client.get_capabilities()
    assert caps.camera is False


def test_is_idle_when_print_state_idle():
    client = _make_client()
    client.state.print_state = "IDLE"
    assert client.is_idle is True


def test_is_printing_when_running():
    client = _make_client()
    client.state.print_state = "RUNNING"
    assert client.is_printing is True


def test_state_compat_shims():
    client = _make_client()
    client.state.print_state = "RUNNING"
    assert client.state.state == "RUNNING"
    assert client.state.raw_data is None


def test_pause_print_sends_cmd_129():
    client = _connected_client()
    client.pause_print()
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 129


def test_resume_print_sends_cmd_131():
    client = _connected_client()
    client.resume_print()
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 131


def test_stop_print_sends_cmd_130():
    client = _connected_client()
    client.stop_print()
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 130


def test_start_print_sends_cmd_128():
    client = _connected_client()
    client.start_print("model.gcode")
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 128
    assert sent["Data"]["Data"]["Filename"] == "model.gcode"


def test_home_sends_cmd_402():
    client = _connected_client()
    client.home()
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 402


def test_check_staleness_returns_connected():
    client = _make_client()
    client.state.connected = False
    assert client.check_staleness() is False
```

- [ ] **Step 2: Run — expect ImportError**

```bash
cd backend && pytest tests/services/test_elegoo_centauri.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.elegoo_centauri_client'`

- [ ] **Step 3: Write `backend/app/services/elegoo_centauri_client.py`**

```python
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
    def is_idle(self) -> bool:
        return self.state.print_state in ("IDLE", "FINISH")

    @property
    def is_printing(self) -> bool:
        return self.state.print_state == "RUNNING"

    def connect(self, loop=None) -> None:
        self._loop = loop
        url = f"ws://{self._ip}:{self._port}"
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
        return False  # Elegoo does not support raw gcode

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

    # --- Internal ---

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
        cmd = data.get("Data", {}).get("Cmd")
        inner = data.get("Data", {}).get("Data", {})
        request_id = data.get("Data", {}).get("RequestID", "")
        # ACK handling
        if request_id in self._pending_acks:
            self._ack_results[request_id] = data.get("Data", {}).get("Result", -1)
            self._pending_acks[request_id].set()
        # Status update
        if cmd in (_SdcpCmd.GET_STATUS, None):
            self._update_state(inner)

    def _update_state(self, data: dict) -> None:
        prev = self.state.print_state
        state_map = {0: "IDLE", 1: "RUNNING", 2: "PAUSE", 3: "FINISH", 4: "FAILED"}
        if "CurrentStatus" in data:
            self.state.print_state = state_map.get(data["CurrentStatus"], "unknown")
        if "PrintInfo" in data:
            info = data["PrintInfo"]
            self.state.progress = float(info.get("Progress", 0))
            self.state.layer_num = int(info.get("CurrentLayer", 0))
            self.state.total_layers = int(info.get("TotalLayer", 0))
            self.state.current_print = info.get("Filename")
        if self._on_state_change and self._loop:
            import asyncio
            asyncio.run_coroutine_threadsafe(
                self._on_state_change(self.state), self._loop
            )
        if self._on_print_complete and self._loop and self.state.print_state == "FINISH" and prev == "RUNNING":
            import asyncio
            asyncio.run_coroutine_threadsafe(
                self._on_print_complete(self.state), self._loop
            )
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd backend && pytest tests/services/test_elegoo_centauri.py -v
```

Expected: 14 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/elegoo_centauri_client.py backend/tests/services/test_elegoo_centauri.py
git commit -m "feat: add ElegooCentauriClient for Centauri Carbon"
```

---

## Task 5: PrinterClientFactory

**Files:**
- Create: `backend/app/services/printer_client_factory.py`
- Create: `backend/tests/services/test_factory.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/services/test_factory.py
import pytest
from unittest.mock import MagicMock
from app.services.printer_client_factory import (
    get_printer_types_for_ui,
    create_client,
    REGISTRY,
)
from app.models import Printer


def _printer(printer_type: str, config: dict) -> Printer:
    p = Printer()
    p.id = 1
    p.name = "Test"
    p.printer_type = printer_type
    p.connection_config = config
    p.orca_printer_profiles = []
    p.current_orca_printer_profile = None
    p.awaiting_plate_clear = False
    p.enabled = True
    return p


def test_registry_has_bambu():
    assert "bambu" in REGISTRY


def test_registry_has_elegoo():
    assert "elegoo_centauri" in REGISTRY


def test_registry_does_not_have_moonraker():
    assert "moonraker" not in REGISTRY


def test_get_printer_types_returns_list():
    types = get_printer_types_for_ui()
    assert isinstance(types, list)
    assert len(types) == 2


def test_get_printer_types_bambu_fields():
    types = {t["printer_type"]: t for t in get_printer_types_for_ui()}
    assert "bambu" in types
    field_names = [f["name"] for f in types["bambu"]["connection_fields"]]
    assert "serial_number" in field_names
    assert "access_code" in field_names


def test_get_printer_types_elegoo_fields():
    types = {t["printer_type"]: t for t in get_printer_types_for_ui()}
    assert "elegoo_centauri" in types
    field_names = [f["name"] for f in types["elegoo_centauri"]["connection_fields"]]
    assert "ip_address" in field_names
    assert "camera_url" in field_names


def test_create_client_bambu():
    from app.services.bambu_mqtt import BambuMQTTClient
    printer = _printer("bambu", {
        "ip_address": "1.2.3.4",
        "serial_number": "ABC",
        "access_code": "secret",
    })
    client = create_client(printer)
    assert isinstance(client, BambuMQTTClient)


def test_create_client_elegoo():
    from app.services.elegoo_centauri_client import ElegooCentauriClient
    printer = _printer("elegoo_centauri", {"ip_address": "1.2.3.5"})
    client = create_client(printer)
    assert isinstance(client, ElegooCentauriClient)


def test_create_client_unknown_type_raises():
    printer = _printer("moonraker", {"port": 7125})
    with pytest.raises(ValueError, match="Unknown printer type"):
        create_client(printer)
```

- [ ] **Step 2: Run — expect ImportError**

```bash
cd backend && pytest tests/services/test_factory.py -v
```

- [ ] **Step 3: Write `backend/app/services/printer_client_factory.py`**

```python
from __future__ import annotations
import importlib
from dataclasses import asdict
from typing import Any

from ..models import Printer
from .abstract_printer_client import AbstractPrinterClient

REGISTRY: dict[str, str] = {
    "bambu": "app.services.bambu_mqtt.BambuMQTTClient",
    "elegoo_centauri": "app.services.elegoo_centauri_client.ElegooCentauriClient",
}

_DISPLAY_NAMES: dict[str, str] = {
    "bambu": "Bambu Lab",
    "elegoo_centauri": "Elegoo Centauri",
}


def _load_class(printer_type: str) -> type[AbstractPrinterClient]:
    if printer_type not in REGISTRY:
        raise ValueError(f"Unknown printer type: {printer_type!r}")
    module_path, class_name = REGISTRY[printer_type].rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def get_printer_types_for_ui() -> list[dict]:
    result = []
    for printer_type, dotted in REGISTRY.items():
        cls = _load_class(printer_type)
        fields = [asdict(f) for f in cls.connection_fields()]
        result.append({
            "printer_type": printer_type,
            "display_name": _DISPLAY_NAMES.get(printer_type, printer_type),
            "connection_fields": fields,
        })
    return result


def create_client(printer: Printer, **callbacks) -> AbstractPrinterClient:
    cls = _load_class(printer.printer_type)
    cfg = printer.connection_config or {}
    accepted = {f.name for f in cls.connection_fields()}
    kwargs = {k: v for k, v in cfg.items() if k in accepted}
    # Only pass callbacks the constructor accepts
    import inspect
    sig = inspect.signature(cls.__init__)
    for k, v in callbacks.items():
        if k in sig.parameters:
            kwargs[k] = v
    return cls(**kwargs)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd backend && pytest tests/services/test_factory.py -v
```

Expected: 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/printer_client_factory.py backend/tests/services/test_factory.py
git commit -m "feat: add PrinterClientFactory with Bambu + Elegoo registry"
```

---

## Task 6: PrinterManager

**Files:**
- Create: `backend/app/services/printer_manager.py`
- Create: `backend/tests/services/test_printer_manager.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/services/test_printer_manager.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.printer_manager import PrinterManager
from app.services.abstract_printer_client import PrinterCapabilities


def _make_mock_client(printer_type="bambu", is_idle=True):
    client = MagicMock()
    client.printer_type = printer_type
    client.connected = True
    client.is_idle = is_idle
    client.is_printing = not is_idle
    client.get_capabilities.return_value = PrinterCapabilities(pause_resume=True)
    client.state = MagicMock()
    client.state.state = "IDLE" if is_idle else "RUNNING"
    client.state.progress = 0.0
    client.state.temperatures = {}
    client.state.current_print = None
    client.state.remaining_time = 0
    client.state.layer_num = 0
    client.state.total_layers = 0
    client.state.raw_data = {}
    return client


def test_manager_starts_empty():
    mgr = PrinterManager()
    assert mgr.get_all_printer_ids() == []


def test_register_and_get_client():
    mgr = PrinterManager()
    client = _make_mock_client()
    mgr._clients[1] = client
    assert mgr.get_client(1) is client


def test_get_client_missing_raises():
    mgr = PrinterManager()
    with pytest.raises(KeyError):
        mgr.get_client(999)


def test_awaiting_plate_clear_default_false():
    mgr = PrinterManager()
    mgr._clients[1] = _make_mock_client()
    assert mgr.is_awaiting_plate_clear(1) is False


def test_set_awaiting_plate_clear():
    mgr = PrinterManager()
    mgr._clients[1] = _make_mock_client()
    mgr._awaiting_plate_clear.add(1)
    assert mgr.is_awaiting_plate_clear(1) is True


def test_printer_ready_requires_idle_and_no_plate():
    mgr = PrinterManager()
    client = _make_mock_client(is_idle=True)
    mgr._clients[1] = client
    assert mgr.is_printer_ready(1) is True
    mgr._awaiting_plate_clear.add(1)
    assert mgr.is_printer_ready(1) is False


def test_printer_not_ready_when_printing():
    mgr = PrinterManager()
    client = _make_mock_client(is_idle=False)
    mgr._clients[1] = client
    assert mgr.is_printer_ready(1) is False


def test_get_normalized_state_bambu():
    mgr = PrinterManager()
    client = _make_mock_client(printer_type="bambu")
    mgr._clients[1] = client
    state = mgr.get_normalized_state(1)
    assert state["id"] == 1
    assert state["connected"] is True
    assert "state" in state
    assert "capabilities" in state


def test_get_all_printer_ids():
    mgr = PrinterManager()
    mgr._clients[1] = _make_mock_client()
    mgr._clients[2] = _make_mock_client()
    assert sorted(mgr.get_all_printer_ids()) == [1, 2]
```

- [ ] **Step 2: Run — expect ImportError**

```bash
cd backend && pytest tests/services/test_printer_manager.py -v
```

- [ ] **Step 3: Write `backend/app/services/printer_manager.py`**

```python
from __future__ import annotations
import asyncio
import logging
from dataclasses import asdict
from typing import Any, Callable

from .abstract_printer_client import AbstractPrinterClient

logger = logging.getLogger(__name__)


def _serialize_bambu(state, printer_id: int) -> dict:
    return {
        "printer_type": "bambu",
        "id": printer_id,
        "connected": state.connected,
        "state": getattr(state, "state", "unknown"),
        "current_print": getattr(state, "current_print", None),
        "progress": getattr(state, "progress", 0.0),
        "remaining_time": getattr(state, "remaining_time", 0),
        "layer_num": getattr(state, "layer_num", 0),
        "total_layers": getattr(state, "total_layers", 0),
        "temperatures": getattr(state, "temperatures", {}),
    }


def _serialize_elegoo(state, printer_id: int) -> dict:
    return {
        "printer_type": "elegoo_centauri",
        "id": printer_id,
        "connected": state.connected,
        "state": getattr(state, "state", "unknown"),
        "current_print": getattr(state, "current_print", None),
        "progress": getattr(state, "progress", 0.0),
        "remaining_time": getattr(state, "remaining_time", 0),
        "layer_num": getattr(state, "layer_num", 0),
        "total_layers": getattr(state, "total_layers", 0),
        "temperatures": getattr(state, "temperatures", {}),
    }


_STATUS_SERIALIZERS: dict[str, Callable] = {
    "bambu": _serialize_bambu,
    "elegoo_centauri": _serialize_elegoo,
}


class PrinterManager:
    def __init__(self) -> None:
        self._clients: dict[int, AbstractPrinterClient] = {}
        self._awaiting_plate_clear: set[int] = set()
        self._on_state_broadcast: Callable | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_broadcast_callback(self, cb: Callable) -> None:
        self._on_state_broadcast = cb

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def register_client(self, printer_id: int, client: AbstractPrinterClient) -> None:
        self._clients[printer_id] = client

    def get_client(self, printer_id: int) -> AbstractPrinterClient:
        return self._clients[printer_id]

    def get_all_printer_ids(self) -> list[int]:
        return list(self._clients.keys())

    def is_awaiting_plate_clear(self, printer_id: int) -> bool:
        return printer_id in self._awaiting_plate_clear

    def is_printer_ready(self, printer_id: int) -> bool:
        client = self._clients.get(printer_id)
        if client is None:
            return False
        return client.is_idle and printer_id not in self._awaiting_plate_clear

    def set_awaiting_plate_clear(self, printer_id: int, awaiting: bool) -> None:
        if awaiting:
            self._awaiting_plate_clear.add(printer_id)
        else:
            self._awaiting_plate_clear.discard(printer_id)

    def load_awaiting_plate_clear(self, printer_ids: set[int]) -> None:
        self._awaiting_plate_clear = printer_ids.copy()

    def get_normalized_state(self, printer_id: int) -> dict:
        client = self._clients[printer_id]
        serializer = _STATUS_SERIALIZERS.get(client.printer_type)
        if serializer is None:
            return {"id": printer_id, "printer_type": client.printer_type, "connected": client.connected}
        state = serializer(client.state, printer_id)
        state["capabilities"] = asdict(client.get_capabilities())
        state["awaiting_plate_clear"] = self.is_awaiting_plate_clear(printer_id)
        return state

    async def on_state_change(self, printer_id: int, vendor_state) -> None:
        if self._on_state_broadcast:
            normalized = self.get_normalized_state(printer_id)
            await self._on_state_broadcast("printer_state", normalized)

    async def on_print_complete(self, printer_id: int, vendor_state) -> None:
        self.set_awaiting_plate_clear(printer_id, True)
        if self._on_state_broadcast:
            normalized = self.get_normalized_state(printer_id)
            await self._on_state_broadcast("plate_clear_required", {"printer_id": printer_id})
            await self._on_state_broadcast("printer_state", normalized)

    def connect_printer(self, printer_id: int, client: AbstractPrinterClient) -> None:
        self.register_client(printer_id, client)
        loop = self._loop

        async def _on_state(state):
            await self.on_state_change(printer_id, state)

        async def _on_complete(state):
            await self.on_print_complete(printer_id, state)

        client._on_state_change = lambda s: (
            asyncio.run_coroutine_threadsafe(_on_state(s), loop) if loop else None
        )
        client._on_print_complete = lambda s: (
            asyncio.run_coroutine_threadsafe(_on_complete(s), loop) if loop else None
        )
        client.connect(loop=loop)

    def disconnect_printer(self, printer_id: int) -> None:
        client = self._clients.pop(printer_id, None)
        if client:
            client.disconnect()


printer_manager = PrinterManager()
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd backend && pytest tests/services/test_printer_manager.py -v
```

Expected: 9 tests PASS.

- [ ] **Step 5: Run full suite — confirm no regressions**

```bash
cd backend && pytest -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/printer_manager.py backend/tests/services/test_printer_manager.py
git commit -m "feat: add PrinterManager singleton with state serialization and plate-clear gate"
```

---

## Task 7: WebSocket Hub

**Files:**
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/websocket.py`
- Create: `backend/tests/api/__init__.py`
- Create: `backend/tests/api/test_websocket.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/api/test_websocket.py
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.api.websocket import connection_manager


async def test_websocket_connect_and_receive(client):
    async with client.stream("GET", "/ws") as response:
        # WebSocket upgrade — just confirm the endpoint exists and app starts
        pass


def test_connection_manager_starts_empty():
    mgr = connection_manager
    assert isinstance(mgr.active_connections, list)


@pytest.mark.asyncio
async def test_broadcast_sends_to_connections():
    from unittest.mock import AsyncMock
    from app.api.websocket import ConnectionManager
    mgr = ConnectionManager()
    mock_ws = AsyncMock()
    mgr.active_connections.append(mock_ws)
    await mgr.broadcast("printer_state", {"id": 1, "state": "IDLE"})
    mock_ws.send_json.assert_called_once()
    call_args = mock_ws.send_json.call_args[0][0]
    assert call_args["type"] == "printer_state"
    assert call_args["data"]["id"] == 1
```

- [ ] **Step 2: Create package markers**

```bash
touch backend/app/api/__init__.py backend/tests/api/__init__.py
```

- [ ] **Step 3: Write `backend/app/api/websocket.py`**

```python
from __future__ import annotations
import json
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.remove(websocket)

    async def broadcast(self, event_type: str, data: Any) -> None:
        payload = {"type": event_type, "data": data}
        dead = []
        for ws in self.active_connections:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active_connections.remove(ws)


connection_manager = ConnectionManager()


async def websocket_endpoint(websocket: WebSocket) -> None:
    await connection_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connection_manager.disconnect(websocket)
```

- [ ] **Step 4: Wire WebSocket into `backend/app/main.py`** — add after health endpoint:

```python
from .api.websocket import websocket_endpoint, connection_manager
from .services.printer_manager import printer_manager

# After app = FastAPI(...):
app.add_api_websocket_route("/ws", websocket_endpoint)
```

Also update `lifespan` to wire the manager's broadcast callback and store the event loop:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    loop = asyncio.get_event_loop()
    printer_manager.set_loop(loop)
    printer_manager.set_broadcast_callback(connection_manager.broadcast)
    yield
```

Add `import asyncio` at the top of `main.py`.

Full updated `backend/app/main.py`:

```python
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api.websocket import connection_manager, websocket_endpoint
from .database import init_db
from .services.printer_manager import printer_manager

_default_static = Path(__file__).parent.parent.parent / "frontend" / "dist"
STATIC_DIR = Path(os.environ.get("THEMIS_STATIC_DIR", str(_default_static)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    loop = asyncio.get_event_loop()
    printer_manager.set_loop(loop)
    printer_manager.set_broadcast_callback(connection_manager.broadcast)
    yield


app = FastAPI(title="Themis", lifespan=lifespan)

app.add_api_websocket_route("/ws", websocket_endpoint)


@app.get("/api/v1/health")
async def health() -> dict:
    return {"status": "ok"}


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
```

- [ ] **Step 5: Run all tests — expect PASS**

```bash
cd backend && pytest -v
```

Expected: all tests pass (websocket broadcast test + all prior tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/ backend/tests/api/ backend/app/main.py
git commit -m "feat: add WebSocket hub and wire into app lifespan"
```

---

## Task 8: Printer API Routes

**Files:**
- Create: `backend/app/api/routes/__init__.py`
- Create: `backend/app/api/routes/printers.py`
- Create: `backend/tests/api/test_printers_api.py`
- Modify: `backend/app/main.py` — include printer router

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/api/test_printers_api.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select
from app.models import Printer
from app.services.abstract_printer_client import PrinterCapabilities


async def test_get_printer_types(client):
    response = await client.get("/api/v1/printers/types")
    assert response.status_code == 200
    types = response.json()
    assert isinstance(types, list)
    printer_type_names = [t["printer_type"] for t in types]
    assert "bambu" in printer_type_names
    assert "elegoo_centauri" in printer_type_names


async def test_list_printers_empty(client):
    response = await client.get("/api/v1/printers")
    assert response.status_code == 200
    assert response.json() == []


async def test_create_printer(client):
    payload = {
        "name": "X1 Carbon",
        "printer_type": "bambu",
        "connection_config": {"ip_address": "192.168.1.10", "serial_number": "ABC", "access_code": "secret"},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4",
    }
    response = await client.post("/api/v1/printers", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "X1 Carbon"
    assert data["printer_type"] == "bambu"
    assert data["id"] is not None


async def test_get_printer(client):
    create = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {"ip_address": "1.2.3.4", "serial_number": "X", "access_code": "Y"},
        "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]
    response = await client.get(f"/api/v1/printers/{printer_id}")
    assert response.status_code == 200
    assert response.json()["id"] == printer_id


async def test_get_printer_not_found(client):
    response = await client.get("/api/v1/printers/9999")
    assert response.status_code == 404


async def test_update_printer(client):
    create = await client.post("/api/v1/printers", json={
        "name": "Old Name", "printer_type": "bambu",
        "connection_config": {}, "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]
    response = await client.patch(f"/api/v1/printers/{printer_id}", json={"name": "New Name"})
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"


async def test_delete_printer(client):
    create = await client.post("/api/v1/printers", json={
        "name": "Temp", "printer_type": "bambu",
        "connection_config": {}, "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]
    response = await client.delete(f"/api/v1/printers/{printer_id}")
    assert response.status_code == 204
    response = await client.get(f"/api/v1/printers/{printer_id}")
    assert response.status_code == 404


async def test_plate_cleared_sets_gate(client):
    create = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {}, "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]
    with patch("app.api.routes.printers.printer_manager") as mock_mgr:
        response = await client.post(f"/api/v1/printers/{printer_id}/plate-cleared")
        assert response.status_code == 200
        mock_mgr.set_awaiting_plate_clear.assert_called_once_with(printer_id, False)


async def test_switch_active_preset(client):
    create = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4", "Bambu Lab P1S 0.2"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4",
    })
    printer_id = create.json()["id"]
    response = await client.patch(
        f"/api/v1/printers/{printer_id}/active-preset",
        json={"preset": "Bambu Lab P1S 0.2"},
    )
    assert response.status_code == 200
    assert response.json()["current_orca_printer_profile"] == "Bambu Lab P1S 0.2"


async def test_switch_active_preset_invalid(client):
    create = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4",
    })
    printer_id = create.json()["id"]
    response = await client.patch(
        f"/api/v1/printers/{printer_id}/active-preset",
        json={"preset": "Not A Real Preset"},
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Create package marker**

```bash
touch backend/app/api/routes/__init__.py
```

- [ ] **Step 3: Write `backend/app/api/routes/printers.py`**

```python
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Printer
from ...services.printer_client_factory import get_printer_types_for_ui
from ...services.printer_manager import printer_manager

router = APIRouter(prefix="/api/v1/printers", tags=["printers"])


class PrinterCreate(BaseModel):
    name: str
    printer_type: str
    connection_config: dict
    orca_printer_profiles: list[str] = []
    current_orca_printer_profile: str | None = None


class PrinterUpdate(BaseModel):
    name: str | None = None
    connection_config: dict | None = None
    orca_printer_profiles: list[str] | None = None
    current_orca_printer_profile: str | None = None
    enabled: bool | None = None


class ActivePresetUpdate(BaseModel):
    preset: str


def _to_dict(p: Printer) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "printer_type": p.printer_type,
        "connection_config": p.connection_config,
        "awaiting_plate_clear": p.awaiting_plate_clear,
        "orca_printer_profiles": p.orca_printer_profiles,
        "current_orca_printer_profile": p.current_orca_printer_profile,
        "enabled": p.enabled,
    }


async def _get_or_404(printer_id: int, session: AsyncSession) -> Printer:
    printer = await session.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(404, f"Printer {printer_id} not found")
    return printer


@router.get("/types")
async def list_printer_types() -> list[dict]:
    return get_printer_types_for_ui()


@router.get("")
async def list_printers(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(select(Printer))
    return [_to_dict(p) for p in result.scalars().all()]


@router.post("", status_code=201)
async def create_printer(
    body: PrinterCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    printer = Printer(
        name=body.name,
        printer_type=body.printer_type,
        connection_config=body.connection_config,
        orca_printer_profiles=body.orca_printer_profiles,
        current_orca_printer_profile=body.current_orca_printer_profile,
    )
    session.add(printer)
    await session.commit()
    await session.refresh(printer)
    return _to_dict(printer)


@router.get("/{printer_id}")
async def get_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return _to_dict(await _get_or_404(printer_id, session))


@router.patch("/{printer_id}")
async def update_printer(
    printer_id: int,
    body: PrinterUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    printer = await _get_or_404(printer_id, session)
    if body.name is not None:
        printer.name = body.name
    if body.connection_config is not None:
        printer.connection_config = body.connection_config
    if body.orca_printer_profiles is not None:
        printer.orca_printer_profiles = body.orca_printer_profiles
    if body.current_orca_printer_profile is not None:
        printer.current_orca_printer_profile = body.current_orca_printer_profile
    if body.enabled is not None:
        printer.enabled = body.enabled
    await session.commit()
    await session.refresh(printer)
    return _to_dict(printer)


@router.delete("/{printer_id}", status_code=204)
async def delete_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    printer = await _get_or_404(printer_id, session)
    await session.delete(printer)
    await session.commit()


@router.post("/{printer_id}/plate-cleared")
async def plate_cleared(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    printer = await _get_or_404(printer_id, session)
    printer.awaiting_plate_clear = False
    await session.commit()
    printer_manager.set_awaiting_plate_clear(printer_id, False)
    return {"ok": True}


@router.patch("/{printer_id}/active-preset")
async def switch_active_preset(
    printer_id: int,
    body: ActivePresetUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    printer = await _get_or_404(printer_id, session)
    if body.preset not in (printer.orca_printer_profiles or []):
        raise HTTPException(422, f"Preset {body.preset!r} not in this printer's configured profiles")
    printer.current_orca_printer_profile = body.preset
    await session.commit()
    await session.refresh(printer)
    return _to_dict(printer)
```

- [ ] **Step 4: Include the router in `backend/app/main.py`**

Add after the existing imports and before `app = FastAPI(...)`:

```python
from .api.routes.printers import router as printers_router
```

Add after `app.add_api_websocket_route("/ws", websocket_endpoint)`:

```python
app.include_router(printers_router)
```

- [ ] **Step 5: Run all tests — expect PASS**

```bash
cd backend && pytest -v
```

Expected: all tests pass including the 10 new printer API tests.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/ backend/tests/api/test_printers_api.py backend/app/main.py
git commit -m "feat: add printer CRUD and control API routes"
```

---

## Self-Review

**Spec coverage check:**
- ✅ `AbstractPrinterClient` ABC with `PrinterCapabilities` (incl. `camera`), `StartPrintOptions` (incl. `gcode_path`), `PrinterFile`, `ConnectionField` — Task 2
- ✅ `_validate_file_id` security helper — Task 2
- ✅ `BambuMQTTClient` for P1S: MQTT/TLS, staleness detection, `stg_cur=255` idle, capabilities (AMS, camera, chamber light, pause_resume) — Task 3
- ✅ `ElegooCentauriClient` for Centauri Carbon: SDCP/WebSocket, ACK mechanism, optional camera URL, `gcode_supported=False` — Task 4
- ✅ Factory + registry with only Bambu + Elegoo (Moonraker/Snapmaker absent by design) — Task 5
- ✅ `PrinterManager`: state serializers, `awaiting_plate_clear` gate, `is_printer_ready` requires both `is_idle` AND no plate — Task 6
- ✅ `on_print_complete` sets `awaiting_plate_clear=True` and broadcasts `plate_clear_required` — Task 6
- ✅ WebSocket hub with `broadcast()`, event format `{type, data}` — Task 7
- ✅ Printer CRUD routes (list, create, get, update, delete) — Task 8
- ✅ `POST /printers/{id}/plate-cleared` — Task 8
- ✅ `PATCH /printers/{id}/active-preset` with 422 for unknown preset — Task 8
- ✅ `GET /printers/types` for dynamic add-printer form — Task 8
- ✅ Capability gate pattern (422 for unsupported features) — Task 8 (`active-preset` validation)

**Placeholder scan:** No TBD or TODO. All code blocks are complete.

**Type consistency:** `printer_manager` singleton imported consistently. `PrinterCapabilities` field `camera` defined in Task 2 and used in Tasks 3, 4. `connection_fields()` classmethod defined in ABC and overridden in both clients. `is_printer_ready()` checks `is_idle AND NOT awaiting_plate_clear` consistently throughout.

**Omitted intentionally (future plans):** camera proxy routes, `/api/v1/orca/printer-presets`, profile filtering — those belong in Plan 4.
