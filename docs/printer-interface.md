# Printer Interface Architecture

This document describes the vendor-agnostic printer abstraction layer in GroundsKeeper. It is detailed enough to recreate the pattern in another codebase or language.

---

## Overview

The system supports multiple 3D printer vendors (Bambu Lab, Elegoo, Moonraker/Klipper, Snapmaker) behind a single abstract interface. The goals are:

1. **No vendor strings in the API layer** — route handlers call `client.start_print()`, not `if bambu: … elif elegoo: …`.
2. **Capability-driven UI** — the frontend reads a capability flags object returned by each client; it never hardcodes printer type strings to decide which controls to show.
3. **Pluggable discovery** — a registry + factory maps a `printer_type` string to a class at runtime; adding a new vendor requires zero changes to route logic.

---

## Data Structures

All structures live in `backend/app/services/abstract_printer_client.py`.

### `PrinterCapabilities` — feature flags

```python
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
```

Every field defaults to `False`. A concrete client overrides only the flags it actually supports. The API serializes this to JSON and the frontend reads it to enable/disable controls — no type string comparisons anywhere.

### `StartPrintOptions` — vendor-agnostic print parameters

```python
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
```

Passed to `start_print()`. Clients ignore fields they don't support; no error is raised for unsupported options.

### `PrinterFile` — normalized file entry

```python
@dataclass
class PrinterFile:
    id: str           # opaque, client-defined, unique within a category
    name: str         # display name only
    size: int         # bytes
    modified_at: str | None = None  # ISO 8601
```

`id` is intentionally opaque — it may be a path, a UUID, or any string the client uses internally. The API never interprets it; it passes it back to `delete_file_by_category()` and `get_file_download_url_by_category()`.

### `ConnectionField` — dynamic connection form descriptor

```python
@dataclass
class ConnectionField:
    name: str           # payload key, e.g. "serial_number"
    label: str          # UI display label
    field_type: str     # "text" | "password" | "number"
    required: bool = True
    default: str | int | None = None
    placeholder: str = ""
    help_text: str = ""
```

Each client class overrides the `connection_fields()` classmethod to return a list of these. The add-printer UI calls `GET /api/v1/printers/types` which reads these descriptors and renders a type-specific form — no frontend hardcoding of per-vendor fields.

---

## The Abstract Base Class

`AbstractPrinterClient` (ABC) lives in `backend/app/services/abstract_printer_client.py`.

### Class-level discriminator

```python
class AbstractPrinterClient(ABC):
    printer_type: ClassVar[str]   # e.g. "bambu", "moonraker", "elegoo_centauri"
```

Every concrete subclass declares this as a class variable. The factory and status serializer use it as a dictionary key — never `isinstance()` checks.

### Connection lifecycle (abstract — must implement)

| Method | Signature | Contract |
|--------|-----------|----------|
| `connected` | `@property → bool` | True when the printer is reachable and reporting status. |
| `connect` | `(loop?) → None` | Establish connection and start background work (thread/async). |
| `disconnect` | `(timeout=0) → None` | Tear down cleanly; stop background thread. |
| `check_staleness` | `() → bool` | Re-evaluate liveness; update `connected` if stale; return current `connected`. Called by the manager before every status read. |

### Print control (abstract — must implement)

| Method | Returns | Contract |
|--------|---------|----------|
| `start_print(file_name, options?)` | `bool` | Submit job. True = accepted by printer. |
| `stop_print()` | `bool` | Cancel active job. |
| `pause_print()` | `bool` | Pause active job. |
| `resume_print()` | `bool` | Resume paused job. |

### Command interface (mixed abstract + default)

| Method | Abstract? | Default behavior |
|--------|-----------|-----------------|
| `send_gcode(gcode)` | **yes** | — |
| `request_status_update()` | **yes** | — |
| `home()` | no | `send_gcode("G28")` |
| `jog_z(distance_mm, force=False)` | no | G91/G1/G90 sequence; `force` wraps with `M211 S0`/`M211 S1` |
| `set_chamber_light(on)` | no | Returns `False` (unsupported) |
| `gcode_supported` | no (property) | Returns `True` |

**Key design point**: `home()` and `jog_z()` have G-code defaults so most clients get them for free. A client that uses a native protocol (e.g. Elegoo's SDCP axis command) overrides them.

### File management (all optional, default no-ops)

| Method | Default return | Notes |
|--------|---------------|-------|
| `file_upload_supported` (property) | `False` | |
| `file_listing_supported` (class attr) | `False` | |
| `upload_file(data, filename)` | `False` | Sync upload |
| `upload_file_async(path, remote_path, progress_cb?, non_retry_exc?)` | `False` | Async upload, vendor protocol |
| `delete_remote_file(remote_path)` | `False` | Pre-upload cleanup |
| `list_files(directory="/")` | `[]` | Legacy flat listing |
| `delete_file(remote_path)` | `False` | Legacy flat delete |
| `storage_info()` | `None` | Usage stats |
| `get_loaded_filaments()` | `[]` | |
| `list_files_by_category(category)` | raises `ValueError` | "models" \| "print_history" \| "timelapse" |
| `delete_file_by_category(category, file_id)` | raises `ValueError` | Validates `file_id` before delegating |
| `get_file_download_url_by_category(category, file_id)` | raises `ValueError` | |

**`_validate_file_id(file_id)`** is a protected helper on the ABC. It rejects null bytes, newlines, path traversal (`..`), absolute paths, tilde prefixes, Windows drive letters (`C:`), and double-encoded traversals (iteratively URL-decodes until stable). Any method that accepts a `file_id` from external input must call this first.

### Capability and lifecycle hooks

| Method | Default | Notes |
|--------|---------|-------|
| `get_capabilities()` | `PrinterCapabilities()` (all False) | Override to expose supported features |
| `on_forced_offline()` | no-op | Called by manager when power loss detected (e.g. smart plug) |
| `is_idle` (property) | `False` | True when printer has no active job |
| `is_printing` (property) | `False` | True when actively printing (includes warmup/leveling/pausing) |

### Category-based file filtering helpers

The ABC provides two class-level lookup tables used by subclasses implementing `list_files_by_category()`:

```python
_FILE_CATEGORY_EXTENSIONS = {
    "models":        frozenset({".3mf", ".stl"}),
    "print_history": frozenset({".gcode", ".gcode.3mf"}),
    "timelapse":     frozenset({".mp4", ".avi", ".mov"}),
}
_FILE_CATEGORY_EXCLUSIONS = {
    "models": frozenset({".gcode.3mf"}),   # exclude sliced archives from model list
}
```

`_filter_by_category(filenames, category)` applies exclusions before extensions and returns the matching subset. Subclasses call this on the raw filename list they get from the printer.

---

## Concrete Implementations

### Pattern every client follows

1. Declare `printer_type = "<string>"` as a class variable.
2. Override `connection_fields()` to list required credentials.
3. Define `__init__` with vendor-specific credentials + optional callbacks. **Constructor signatures are intentionally NOT part of the interface** — the factory handles the mapping.
4. Hold a `self.state` object (vendor-specific dataclass) that is updated by background communication.
5. Implement all abstract methods. Override optional methods for supported features.
6. Override `get_capabilities()` to return a `PrinterCapabilities` with the right flags set.

### `BambuMQTTClient` — `bambu_mqtt.py`

**Protocol**: MQTT over TLS, port 8883.

**State type**: `PrinterState` (large dataclass with AMS data, K-profiles, HMS errors, fan speeds, etc.)

**Connection**: `paho-mqtt`. Background thread handles all I/O. Subscribes to `device/<serial>/report`. Publishes commands to `device/<serial>/request`.

**Mock seam** (tests): `client._client = MagicMock(); client.state.connected = True`. Assert via `client._client.publish.call_args`.

**Key implementation notes**:
- All MQTT publishes use `qos=1` — the printer silently drops `qos=0` while broadcasting status.
- `set_chamber_light(on)` publishes **twice** — one for `"chamber_light"` and one for `"chamber_light2"` LED nodes.
- `home()` inherits the ABC default (G28 via `send_gcode`).
- Staleness detection: if no message received for `STALE_TIMEOUT = 60s`, `check_staleness()` force-closes the socket to trigger reconnect, with a `STALE_RECONNECT_COOLDOWN = 30s` guard to prevent rapid cycling.
- `connection_fields()` returns `serial_number` (text) and `access_code` (password).

**`PrinterState` highlights**:
- `state: str` — `"IDLE"`, `"RUNNING"`, `"PAUSE"`, `"FINISH"`, `"FAILED"`, `"unknown"`
- `stg_cur: int` — calibration stage index; -1 = idle (X1), 255 = idle (A1/P1)
- `temperatures: dict` — `{"nozzle": float, "bed": float, "chamber": float, …}`
- `raw_data: dict` — raw MQTT push_status payload (AMS data is parsed from here)
- `hms_errors: list[HMSError]` — active health management errors

### `ElegooCentauriClient` — `elegoo_centauri_client.py`

**Protocol**: SDCP (Chitubox Data Communication Protocol) over WebSocket, port 3030.

**State type**: `ElegooState`

**Connection**: `websocket-client` library. Background daemon thread runs `ws.run_forever()` with auto-reconnect.

**Command format**:
```json
{
  "Id": "",
  "Data": {
    "Cmd": <int>,
    "Data": {},
    "RequestID": "<uuid>",
    "MainboardID": "<id>",
    "TimeStamp": <unix_sec>,
    "From": 1
  }
}
```

**Key SDCP command IDs**:
| ID | Name | Purpose |
|----|------|---------|
| 0 | GET_STATUS | Request full status |
| 1 | GET_ATTR | Request static attributes |
| 128 | START_PRINT | `{"Filename": "<path>"}` |
| 129 | SUSPEND_PRINT | Pause |
| 130 | STOP_PRINT | Cancel |
| 131 | RESTORE_PRINT | Resume |
| 401 | EDIT_AXIS_NUMBER | Jog Z: `{"Axis": "Z", "Step": <mm>}` |
| 402 | EDIT_AXIS_ZERO | Home: `{"Axis": "XYZ"}` |
| 403 | EDIT_STATUS_DATA | Chamber light, fans |
| 258 | GET_FILE_LIST | `{"Url": "/local/"}` |
| 259 | DELETE_FILE | `{"FileList": [...]}` |

**ACK mechanism**: `_send(cmd, data, wait_ack=True)` registers a `threading.Event` in `_pending_acks[request_id]` before calling `ws.send()`. The response handler fires the event and stores the result code in `_ack_results[request_id]`.

**Mock seam** (tests): `client._ws = MagicMock()`. Use a `side_effect` on `ws.send` that injects `client._ack_results[request_id] = 0` and fires the event in `client._pending_acks[request_id]`.

**Override notes**:
- `home()` uses CMD 402 (native axis zero) instead of G28.
- `jog_z()` uses CMD 401 instead of G-code.
- `set_chamber_light()` sends CMD 403 with `LightStatus.SecondLight`.
- `connection_fields()` returns only `port` (number, default 3030).

**`ElegooState` compat shims**: `raw_data` property always returns `None` (no AMS), `state` property returns `print_state` — both exist so generic code that reads Bambu fields doesn't crash.

### `MoonrakerClient` — `moonraker_client.py`

**Protocol**: HTTP REST. Background thread polls `objects/query` every 5 seconds.

**State type**: `MoonrakerState` (similar compat shims: `raw_data → None`, `state → print_state`).

**State mapping**: Moonraker's `print_state` strings (`"printing"`, `"paused"`, `"complete"`, etc.) are normalized to Bambu-compatible names (`"RUNNING"`, `"PAUSE"`, `"FINISH"`) by the `_moonraker_state_to_dict` serializer in `printer_manager.py`.

**Commands**: `start_print` sends a POST to `/printer/print/start`, `stop_print` to `/printer/print/cancel`, etc. All are synchronous `httpx` calls (fast/small commands, safe to block briefly).

**`SnapmakerU1Client`** subclasses `MoonrakerClient` without change — Snapmaker U1 runs Klipper/Moonraker and requires no overrides.

---

## The Factory

`backend/app/services/printer_client_factory.py`

### Registry

```python
_REGISTRY: dict[str, str] = {
    "bambu":           "backend.app.services.bambu_mqtt.BambuMQTTClient",
    "moonraker":       "backend.app.services.moonraker_client.MoonrakerClient",
    "elegoo_centauri": "backend.app.services.elegoo_centauri_client.ElegooCentauriClient",
    "snapmaker_u1":    "backend.app.services.snapmaker_u1_client.SnapmakerU1Client",
}
```

Classes are imported lazily via `importlib.import_module` to avoid circular imports at module load time. Adding a new vendor = add one entry here; nothing else changes.

### `create_client(printer, **callbacks) → AbstractPrinterClient`

Takes an ORM `Printer` row and optional callback functions, dispatches to the right constructor. Constructor signatures differ per vendor (Bambu needs `serial_number` + `access_code`; Moonraker needs `port` + `api_key`). The factory holds all that knowledge centrally.

Bambu-specific callbacks (`on_ams_change`, `on_bed_temp_update`) are silently dropped for non-Bambu clients — the factory doesn't pass kwargs the constructor doesn't accept.

### `get_printer_types_for_ui() → list[dict]`

Reads `connection_fields()` from each registered class and returns:
```json
[
  {
    "printer_type": "bambu",
    "display_name": "Bambu Lab",
    "connection_fields": [{"name": "serial_number", ...}, {"name": "access_code", ...}]
  },
  ...
]
```

Called by `GET /api/v1/printers/types`. The add-printer UI renders a dynamic form from this — no frontend code needs to know what credentials each vendor requires.

---

## The Manager

`backend/app/services/printer_manager.py` — singleton `printer_manager` used throughout the app.

### Internal state

```python
_clients: dict[int, AbstractPrinterClient]   # keyed by DB printer ID
_models: dict[int, str | None]               # printer model string cache
_printer_info: dict[int, PrinterInfo]        # name + serial/device-id cache
_awaiting_plate_clear: set[int]              # gate: must confirm plate before next job
_current_print_user: dict[int, dict]         # who started the current print
```

### Callback wiring

The manager owns five event callbacks registered at startup:

| Callback | Signature | Trigger |
|----------|-----------|---------|
| `on_state_change` | `(printer_id, vendor_state)` | Any status update from the printer |
| `on_print_start` | `(printer_id, data)` | Print job begins |
| `on_print_complete` | `(printer_id, data)` | Print job finishes |
| `on_ams_change` | `(printer_id, ams_list)` | AMS filament data changes (Bambu only) |
| `on_layer_change` | `(printer_id, layer_num)` | Layer counter increments |

`connect_printer(printer)` creates closure wrappers that inject `printer_id` and calls `create_client(printer, on_state_change=…, …)`.

All callbacks are dispatched from background threads via `asyncio.run_coroutine_threadsafe(coro, loop)`, captured in a `Future` with a done-callback that logs exceptions (prevents silent failures).

### Model-based feature detection

Because some feature support is model-specific rather than type-specific (e.g. chamber temperature sensor presence, AMS drying firmware requirements), the manager holds model strings and exposes free functions:

```python
def supports_chamber_temp(model: str | None) -> bool: ...
def is_bed_slinger(model: str | None) -> bool: ...
def supports_drying(model: str | None, firmware: str | None) -> bool: ...
def has_stg_cur_idle_bug(model: str | None) -> bool: ...
```

These are applied by the `printer_state_to_dict` serializer (e.g. filter out `chamber` temps for models without a real sensor; apply the `stg_cur=0` idle bug workaround for A1/P1P/P1S).

### Awaiting-plate-clear gate

`_awaiting_plate_clear: set[int]` is persisted to the database (`printers.awaiting_plate_clear`) so the gate survives restarts and power cycles. Managed via:
- `set_awaiting_plate_clear(printer_id, awaiting)` — updates the in-memory set, async-schedules a DB write and a WebSocket broadcast
- `load_awaiting_plate_clear_from_db()` — called at startup to rehydrate

---

## State Serialization

Each vendor has a vendor-specific state dataclass (`PrinterState`, `ElegooState`, `MoonrakerState`). The API and WebSocket push need a **normalized JSON structure** that the frontend can consume uniformly.

### Per-type serializers (in `printer_manager.py`)

```python
_STATUS_SERIALIZERS: dict[str, Callable] = {
    "bambu":         lambda state, printer_id, model: printer_state_to_dict(state, printer_id, model),
    "elegoo_centauri": lambda state, printer_id, model: _elegoo_state_to_dict(state, printer_id),
    "moonraker":     lambda state, printer_id, model: _moonraker_state_to_dict(state, printer_id),
    "snapmaker_u1":  lambda state, printer_id, model: _moonraker_state_to_dict(state, printer_id),
}
```

Adding a new vendor = add one entry here. The broadcast path looks up by `client.printer_type` — no `isinstance`.

### Normalized output fields (common subset)

All serializers produce at minimum:

```json
{
  "printer_type": "bambu",
  "id": 1,
  "connected": true,
  "state": "RUNNING",
  "current_print": "my_model.3mf",
  "progress": 42.5,
  "remaining_time": 30,
  "layer_num": 12,
  "total_layers": 80,
  "temperatures": {"nozzle": 220.0, "bed": 60.0},
  "cover_url": "/api/v1/printers/1/cover",
  "capabilities": { ... }
}
```

`state` is always one of: `"IDLE"`, `"RUNNING"`, `"PAUSE"`, `"FINISH"`, `"FAILED"`, `"unknown"`. Moonraker and Elegoo map their native state strings to this set in their serializers.

---

## API Capability Gate Pattern

Route handlers check capabilities before calling through, returning **422** (not 500) for unsupported features:

```python
# printers.py
@router.post("/{printer_id}/chamber-light")
async def set_chamber_light(printer_id: int, on: bool, ...):
    client = printer_manager.get_client(printer_id)
    if not client.get_capabilities().chamber_light:
        raise HTTPException(422, "This printer does not support chamber light control")
    success = client.set_chamber_light(on)
    if not success:
        raise HTTPException(500, "Failed to control chamber light")
```

The 422 is an intentional choice: it tells the frontend the operation is not applicable to this device (a client error), not that something broke on the server. The frontend only shows controls for which the capability flag is `true`, so this path should rarely be hit in practice.

---

## Adding a New Vendor — Checklist

1. Create `backend/app/services/<vendor>_client.py`.
2. Define a vendor state dataclass (add `raw_data → None` and `state → print_state` compat properties if your state doesn't match Bambu's).
3. Subclass `AbstractPrinterClient`.
4. Set `printer_type = "<vendor>"`.
5. Override `connection_fields()` with required credentials.
6. Implement all `@abstractmethod` methods.
7. Override optional methods for supported features (`set_chamber_light`, `upload_file_async`, etc.).
8. Override `get_capabilities()` to return a `PrinterCapabilities` with your flags set.
9. Override `is_idle` and `is_printing` properties if your state enum has clean mappings.
10. Add an entry to `_REGISTRY` in `printer_client_factory.py`.
11. Add a serializer entry to `_STATUS_SERIALIZERS` in `printer_manager.py`.
12. Add to `_UI_TYPES` in `printer_client_factory.py` if it should appear in the add-printer UI.
13. Add a branch in `create_client()` if constructor kwargs differ from the Moonraker defaults.
14. Add a branch in `PrinterManager.test_connection()` if you want connection testing without persistence.

---

## Testing Patterns

### Unit tests — Bambu

```python
client = BambuMQTTClient(ip_address="1.2.3.4", serial_number="X", access_code="Y")
client._client = MagicMock()
client.state.connected = True

client.set_chamber_light(True)

payload = json.loads(client._client.publish.call_args[0][1])
```

### Unit tests — Elegoo

```python
def _make_ack_responder(client):
    def side_effect(msg_str):
        data = json.loads(msg_str)
        request_id = data["Data"]["RequestID"]
        client._ack_results[request_id] = 0          # success
        event = client._pending_acks.get(request_id)
        if event:
            event.set()
    return side_effect

client._ws = MagicMock()
client._ws.send.side_effect = _make_ack_responder(client)
```

### Integration tests — API routes

```python
with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
    mock_client = MagicMock(spec=AbstractPrinterClient)
    mock_client.get_capabilities.return_value = PrinterCapabilities(chamber_light=True)
    mock_pm.get_client.return_value = mock_client

    response = client.post("/api/v1/printers/1/chamber-light?on=true")
    assert response.status_code == 200
```
