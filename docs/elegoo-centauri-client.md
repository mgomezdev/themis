# Elegoo Centauri Client — Implementation Reference

Source: `backend/app/services/elegoo_centauri_client.py`  
Tests: `backend/tests/unit/services/test_elegoo_centauri_client.py`

---

## Protocol Overview

The Elegoo Centauri Carbon uses **SDCP** (Chitubox Data Communication Protocol), a proprietary protocol originally from Chitubox resin slicers that Elegoo adopted for their FFF line.

Transport: **WebSocket** at `ws://<ip>:3030/websocket` (port is firmware-fixed, exposed as a configurable default).

The connection is bidirectional. The printer publishes unsolicited pushes on MQTT-style topic strings embedded in the JSON envelope. The client sends commands using numeric command IDs.

---

## Wire Format

### Outgoing commands (client → printer)

```json
{
  "Id": "",
  "Data": {
    "Cmd": 128,
    "Data": { "Filename": "/local/cube.gcode" },
    "RequestID": "a3f1b2c4d5e6...",
    "MainboardID": "MAINBOARD_SERIAL",
    "TimeStamp": 1716220800,
    "From": 1
  }
}
```

- `Id` — always empty string
- `Cmd` — integer command code (see table below)
- `Data` — command-specific payload dict (empty `{}` if no parameters)
- `RequestID` — `uuid4().hex` generated per send; used to match ACK responses
- `MainboardID` — printer's mainboard serial (learned from `GET_ATTR` on connect; empty string is accepted before it's known)
- `TimeStamp` — Unix timestamp in seconds
- `From` — always `1` (client identifier)

### Incoming messages (printer → client)

All messages have a `Topic` field that determines the message type:

| Topic pattern | Purpose |
|---|---|
| `sdcp/status/<MainboardID>` | Live state push (temps, progress, light, fans) |
| `sdcp/attributes/<MainboardID>` | Static info push (firmware version, model name) |
| `sdcp/response/<MainboardID>` | ACK for a command the client sent |
| `sdcp/error/<MainboardID>` | Error notification |

---

## Command ID Reference

| ID | Constant | Direction | Payload | Notes |
|----|----------|-----------|---------|-------|
| 0 | `_CMD_GET_STATUS` | → printer | `{}` | Request immediate status push |
| 1 | `_CMD_GET_ATTR` | → printer | `{}` | Request attributes push |
| 128 | `_CMD_START_PRINT` | → printer | `{"Filename": "<path>"}` | |
| 129 | `_CMD_SUSPEND_PRINT` | → printer | `{}` | Pause |
| 130 | `_CMD_STOP_PRINT` | → printer | `{}` | Cancel |
| 131 | `_CMD_RESTORE_PRINT` | → printer | `{}` | Resume |
| 134 | `_CMD_GET_BLACKOUT` | → printer | `{}` | Query power-cut status |
| 135 | `_CMD_SEND_BLACKOUT` | printer → | — | Printer-initiated only |
| 258 | `_CMD_GET_FILE_LIST` | → printer | `{"Url": "/local/"}` | Returns `FileList` array |
| 259 | `_CMD_DELETE_FILE` | → printer | `{"FileList": [...], "FolderList": [...]}` | |
| 386 | `_CMD_EDIT_VIDEO_STREAMING` | → printer | `{"Enable": 1}` or `{"Enable": 0}` | Start/stop MJPEG stream |
| 401 | `_CMD_EDIT_AXIS_NUMBER` | → printer | `{"Axis": "Z", "Step": <mm>}` | Jog axis |
| 402 | `_CMD_EDIT_AXIS_ZERO` | → printer | `{"Axis": "XYZ"}` | Home |
| 403 | `_CMD_EDIT_STATUS_DATA` | → printer | See write payloads below | Light, fan, temperature control |

### Cmd 403 write payloads (`EDIT_STATUS_DATA`)

| Purpose | Payload |
|---|---|
| Chamber light (on) | `{"LightStatus": {"SecondLight": true, "RgbLight": [R, G, B]}}` |
| Chamber light (off) | `{"LightStatus": {"SecondLight": false, "RgbLight": [R, G, B]}}` |
| Fan speeds | `{"TargetFanSpeed": {"ModelFan": 80, "AuxiliaryFan": 60, "BoxFan": 40}}` |
| Bed temperature | `{"TempTargetHotbed": 95}` — use `0` to turn off |
| Nozzle temperature | `{"TempTargetNozzle": 220}` — deferred, do not expose yet |
| Print speed | `{"PrintSpeedPct": 100}` — deferred |

**Fan semantics:** All three fan values must be sent together. To change one fan without resetting others, read the current `fan_model`/`fan_aux`/`fan_box` from the state dict, patch the target fan, then send all three.

---

## Status Message Parsing

### `CurrentStatus` array

The top-level `Status.CurrentStatus` is an array of active state codes:

| Code | Meaning |
|------|---------|
| 0 | Idle / standby |
| 1 | Printing (check `PrintInfo.Status` for sub-state) |
| 8 | Print complete |

**Rule**: `CurrentStatus=[8]` is the authoritative "complete" signal and overrides everything else. For all other states, `PrintInfo.Status` is the primary discriminator.

### `PrintInfo.Status` sub-state codes

| Code | `print_state` string | Meaning |
|------|----------------------|---------|
| 0 | `"standby"` | Idle |
| 1 | `"warming_up"` | Warmup or cancellation warmup |
| 5 | `"pausing"` | Mid-pause transition |
| 6 | `"paused"` | Fully paused |
| 8 | `"cancelled"` | Cancelled |
| 9 | `"complete"` | Finished (alternative path to code 8) |
| 13 | `"printing"` | Actively printing |
| 14 | `"cancelled"` | Alternate cancelled code |
| 20 | `"leveling"` | Bed leveling sub-state |

Fallback: if `PrintInfo.Status` is not in the map but `CurrentStatus=[1]`, `print_state` is set to `"printing"`. If nothing matches, `"standby"`.

### Temperature field mapping

SDCP key → normalized field name:

| SDCP | Normalized |
|------|-----------|
| `TempOfNozzle` | `nozzle` |
| `TempTargetNozzle` | `nozzle_target` |
| `TempOfHotbed` | `bed` |
| `TempTargetHotbed` | `bed_target` |
| `TempOfBox` | `chamber` |
| `TempTargetBox` | `chamber_target` |

All values are rounded to 1 decimal place. Missing keys are omitted (not defaulted to zero).

### Fan fields

From `Status.CurrentFanSpeed`:
- `ModelFan` → `state.fan_model` (part-cooling fan, 0–100)
- `AuxiliaryFan` → `state.fan_aux` (0–100)
- `BoxFan` → `state.fan_box` (box/chamber fan, 0–100)

### Light fields

From `Status.LightStatus`:
- `SecondLight` → `state.chamber_light` (bool; the work/chamber light)
- `RgbLight` → `state.rgb_light` (list `[R, G, B]`; accent lighting)

**Critical**: `RgbLight` is only updated when it appears in the message. If the field is absent, `state.rgb_light` retains its previous value. This prevents `set_chamber_light` from accidentally resetting the accent color.

### Persistent fields carried forward

`_parse_status_msg` builds a fresh `ElegooState` from scratch each call. Three fields are **not** in the status message and must be copied from the existing state under the lock:

- `state.firmware_version`
- `state.machine_name`
- `state.mainboard_id`

---

## `ElegooState` Dataclass

```python
@dataclass
class ElegooState:
    connected: bool = False
    current_status: list[int]     # raw CurrentStatus array
    print_state: str              # "standby"|"warming_up"|"leveling"|"printing"|"pausing"|"paused"|"cancelled"|"complete"
    filename: str | None          # active print filename
    task_id: str | None           # active task ID
    progress: float               # 0.0–100.0
    layer_num: int | None         # current layer
    total_layers: int | None
    current_ticks: float          # elapsed seconds
    total_ticks: float            # estimated total seconds
    print_speed_pct: int          # e.g. 100
    temperatures: dict            # normalized (see above)
    fan_model: int                # 0–100
    fan_aux: int                  # 0–100
    fan_box: int                   # 0–100, box/chamber fan
    chamber_light: bool           # SecondLight
    rgb_light: list               # [R, G, B]
    video_url: str | None         # populated by Cmd 386 response
    firmware_version: str | None  # from sdcp/attributes
    machine_name: str | None      # from sdcp/attributes
    mainboard_id: str | None      # from sdcp/attributes
    raw: dict                     # full message (compare=False — never suppresses change detection)
```

**Compat shims** (so generic code that reads Bambu fields doesn't crash):
- `raw_data` property → always `None` (signals "no AMS data" to AMS-processing code)
- `state` property → returns `print_state` (used for logging and dedup keys)

---

## ACK / Request-Response Mechanism

All commands that need a confirmation use `wait_ack=True` in `_send()`.

### Flow

1. `_send()` generates a `request_id = uuid4().hex` and creates `event = threading.Event()`.
2. Before calling `ws.send()`, it registers `_pending_acks[request_id] = event`.
3. `ws.send(payload)` is called from the calling thread.
4. `_send()` calls `event.wait(timeout=10.0)`.
5. In the WebSocket background thread, `_on_ws_message` fires:
   - `sdcp/response` topic → `_parse_response_msg()` looks up `_pending_acks[request_id]`, stores `_ack_results[request_id] = ack`, stores full response data in `_response_data[request_id]`, then calls `event.set()`.
   - `sdcp/error` topic → `_parse_error_msg()` does the same with the error ack, preventing the caller from hanging until timeout on a rejected command.
6. `_send()` wakes up, reads `_ack_results.pop(request_id, -1)`, returns `True` if ack == 0.

### `_send_with_response()`

For commands that need both the ACK result and the response payload (e.g., `GET_FILE_LIST`), use `_send_with_response()`. Returns `(success: bool, data: dict)` where `data` is the `Data.Data` inner dict from the response envelope.

### Timeout behavior

- Default timeout: 10 seconds.
- On timeout: cleans up `_pending_acks`, `_ack_results`, `_response_data`, logs at DEBUG, returns `False`.
- All dict cleanup happens whether timed out or not, preventing memory leaks.

---

## Thread Architecture

### Threads started by `connect()`

| Thread name | Target | Purpose |
|---|---|---|
| `elegoo-<ip>` | `_run_ws()` | WebSocket connection + reconnect loop |
| `elegoo-ka-<ip>` | `_run_sdcp_keepalive()` | Sends `GET_STATUS` (Cmd 0) every 50 seconds |

Both threads are daemon threads (terminated when main process exits).

`loop` parameter accepted by `connect()` for ABC compatibility but not used — SDCP is fully synchronous/threaded, no asyncio.

### `_run_ws()` — reconnect loop

```
while not stop_event:
    create WebSocketApp(url, callbacks...)
    self._ws = ws
    ws.run_forever(ping_interval=30, ping_timeout=10)
    # run_forever returns on close or error
    self._ws = None
    if not stop_event:
        stop_event.wait(5.0)   # 5-second reconnect delay
```

On unexpected close: `_on_ws_close` sets `state.connected = False` and fires `on_state_change`. The loop then waits 5 seconds and reconnects.

On `disconnect()`: `stop_event.set()` + `ws.close()` causes `run_forever` to return, then `stop_event.is_set()` prevents re-entry.

### Threading lock

`self._lock` (a `threading.Lock`) guards:
- All reads/writes of `self.state` that cross thread boundaries
- `self._mainboard_id`
- `self.state.video_url`

`_parse_status_msg()` reads `state.firmware_version`, `state.machine_name`, and `self._mainboard_id` under the lock, then the caller assigns the entire `self.state` under the lock after parsing.

---

## Connection Lifecycle

### `connect()`

1. Clears `_stop_event`.
2. Starts `elegoo-<ip>` thread running `_run_ws()`.
3. Starts `elegoo-ka-<ip>` thread running `_run_sdcp_keepalive()`.

### `_on_ws_open()`

Called by `run_forever` immediately after the WebSocket handshake succeeds. Sends two commands synchronously on the WebSocket:
1. `GET_STATUS` (Cmd 0) — gets current printer state immediately
2. `GET_ATTR` (Cmd 1) — gets firmware version, machine name, mainboard ID

`state.connected` becomes `True` on the first `sdcp/status` message received (set inside `_parse_status_msg`).

### `check_staleness()`

Returns `self.state.connected`. No active staleness detection — the WebSocket `ping_interval=30` / `ping_timeout=10` parameters let `run_forever` handle it natively. If the connection drops, `_on_ws_close` fires and `connected` goes `False`.

### `disconnect()`

1. Sets `_stop_event` to prevent reconnect.
2. Calls `ws.close()` to terminate `run_forever`.
3. Joins the WS thread (waits at least 3 seconds).
4. Sets `state.connected = False` under lock.

---

## Event Callbacks

Fired from within `_on_ws_message()` (WebSocket background thread):

| Callback | When | Payload |
|---|---|---|
| `on_state_change(state)` | Any status message where `new_state != self.state` | `ElegooState` |
| `on_print_start(data)` | `print_state` transitions to `"printing"` from any other state | `{"filename": str}` |
| `on_print_complete(data)` | `print_state` transitions to `"complete"` from `"printing"` | `{"filename": str}` |
| `on_layer_change(layer_num)` | `layer_num` changes and is > 0 | `int` |

**State change dedup**: `ElegooState.__eq__` compares all fields. The `raw` field is excluded from equality (`compare=False`) so identical raw payloads with different raw dicts don't suppress callbacks for real state changes.

**Print event tracking**: `_prev_print_state` persists across messages (instance variable, not in state). The transition guard `prev != "printing"` for `on_print_start` prevents re-firing if a "printing" message arrives while already printing.

---

## Chamber Light

```python
def set_chamber_light(self, on: bool) -> bool:
    with self._lock:
        rgb = list(self.state.rgb_light)
    success = self._send(
        _CMD_EDIT_STATUS_DATA,
        {"LightStatus": {"SecondLight": on, "RgbLight": rgb}},
        wait_ack=True,
    )
    if success:
        with self._lock:
            self.state.chamber_light = on
    return success
```

**Cmd 403** (`EDIT_PRINTER_STATUS_DATA`) takes the full `LightStatus` object. Sending only `SecondLight` without `RgbLight` resets the accent light to off/black. The current `rgb_light` value is read under the lock and echoed back to preserve it.

`state.chamber_light` is updated optimistically only on ACK success (ack == 0). On error, the state stays unchanged.

---

## Axis Control

Elegoo does not expose a raw G-code channel (`gcode_supported = False`, `send_gcode()` returns `False` without calling `ws.send()`). Axis operations use native SDCP commands.

### `home()`

Sends Cmd 402 (`EDIT_PRINTER_AXIS_ZERO`) with `{"Axis": "XYZ"}`. Overrides the ABC default (`send_gcode("G28")`).

### `jog_z(distance_mm)`

Sends Cmd 401 (`EDIT_PRINTER_AXIS_NUMBER`) with `{"Axis": "Z", "Step": distance_mm}`. The `force` parameter from the ABC signature is accepted but silently ignored (no soft endstop concept on Elegoo). Negative values move the axis in the negative direction.

---

## File Management

### Upload (sync)

`upload_file(file_data: bytes, filename: str) → bool`

HTTP multipart POST to `http://<ip>:3030/uploadFile/upload`.

Fields:
| Field | Value |
|---|---|
| `TotalSize` | `str(len(file_data))` |
| `Uuid` | `uuid4().hex` |
| `Offset` | `"0"` |
| `Check` | `"1"` |
| `S-File-MD5` | MD5 hex digest of file_data |
| `File` | multipart file part, `application/octet-stream` |

Success detection: `result.get("success") is True` OR `result.get("code") == "000000"`.

Timeout: 120 seconds (large file tolerance).

### Upload (async)

`upload_file_async()` reads the file into bytes, then calls `loop.run_in_executor(None, self.upload_file, ...)` — wraps the sync HTTP upload in a thread pool executor.

### Delete

`delete_file(remote_path)` sends Cmd 259 with `{"FileList": [remote_path], "FolderList": []}`.

### List

`list_files(directory)` sends Cmd 258 with `{"Url": directory}` via `_send_with_response`. Returns list of `{"name", "size", "path", "is_directory": False}` dicts.

**Root path remapping**: The generic file manager starts with `"/"`. Elegoo's actual filesystem root is `"/local/"`. `list_files("/")` transparently remaps to `"/local/"`.

### Category-based listing

`_SDCP_CATEGORY_PATHS` maps logical categories to the SDCP path:

```python
_SDCP_CATEGORY_PATHS = {
    "models":        "/local/",
    "print_history": "/local/",
}
```

Both categories use the same SDCP path; filtering happens via the ABC's `_filter_by_category()` helper using the extension tables:

- `"models"` → `.3mf`, `.stl` (excludes `.gcode.3mf`)
- `"print_history"` → `.gcode`, `.gcode.3mf`
- `"timelapse"` → **not supported** (raises `ValueError`)

File `id` is the bare filename (e.g. `"cube.3mf"`). `delete_file_by_category` prepends the SDCP path: `/local/cube.3mf`.

`get_file_download_url_by_category` raises `ValueError` for all categories — Elegoo's HTTP download path is not yet known (tracked in GitHub issue #26).

---

## Capabilities

```python
PrinterCapabilities(
    ams=False,
    file_upload=True,
    bed_levelling=True,
    flow_calibration=False,
    vibration_cali=False,
    layer_inspect=False,
    timelapse=False,
    chamber_light=True,
    gcode=False,
    pause_resume=True,
    skip_objects=False,
    multi_nozzle=False,
    file_models=True,
    file_history=True,
    file_timelapse=False,
)
```

---

## State Serialization (in `printer_manager.py`)

`_elegoo_state_to_dict(state, printer_id)` normalizes `ElegooState` to the common JSON structure:

- `state` field: maps `print_state` via `_SDCP_STATE_MAP`:
  ```python
  {"printing": "RUNNING", "paused": "PAUSE", "complete": "FINISH", "standby": "IDLE"}
  ```
  Unmapped states are `.upper()`-ed.
- `remaining_time` (minutes): computed as `int((total_ticks - current_ticks) / 60)` when `total_ticks > 0` and `current_ticks < total_ticks`. Otherwise `None`.
- `speed_factor`: `print_speed_pct / 100.0`
- `fan_speed`: maps `fan_model` (0–100 integer)
- `klippy_state`: `"ready"` if connected, `"disconnected"` otherwise (for frontend compat)
- `cover_url`: always `None` (no thumbnail support)

---

## Video Streaming

Cmd 386 (`EDIT_PRINTER_VIDEO_STREAMING`) toggles the MJPEG stream.

### `start_video_stream(timeout=5.0) → str`

1. Clears `_video_url_event` and `_pending_video_url`.
2. Sends Cmd 386 with `{"Enable": 1}` (fire-and-forget, no ACK wait).
3. Waits up to `timeout` seconds for `_video_url_event` to be set by `_parse_response_msg`.
4. Returns `_pending_video_url` if received, otherwise falls back to `http://<ip>:3031/video`.
5. Appends `?timestamp=<unix>` to bust caches.

`_parse_response_msg` handles the Cmd 386 response: if `VideoUrl` is present in the response and non-empty, uses it; otherwise constructs the fallback. Non-`http` URLs are prefixed with `http://`.

### `ping_video_stream()`

Re-sends `{"Enable": 1}` to reset the printer's 60-second inactivity timer. Must be called periodically while streaming.

### `stop_video_stream()`

Sends `{"Enable": 0}`.

---

## `is_idle` and `is_printing`

```python
@property
def is_idle(self) -> bool:
    return self.state.print_state in ("standby", "complete", "cancelled")

@property
def is_printing(self) -> bool:
    return self.state.print_state in ("printing", "warming_up", "leveling", "pausing")
```

`paused` is intentionally neither idle nor printing — the queue must not dispatch to a paused printer.

---

## `get_loaded_filaments()`

Returns a single-element list with a placeholder external spool. Elegoo has no filament RFID or type detection:

```python
[{"type": "", "color": "#808080", "tray_info_idx": "", "tray_sub_brands": "", "extruder_id": None, "is_external": True}]
```

---

## Connection Test (class-level)

`ElegooCentauriClient.test_connection(ip_address, port, api_key) → dict`

A classmethod that opens a temporary WebSocket, sends `GET_ATTR`, waits up to 8 seconds for an `sdcp/attributes` response, then closes. Returns:
```json
{"success": true, "state": "ready", "model": "Centauri Carbon"}
```
Or on failure: `{"success": false, "error": "<message>"}`.

Used by `PrinterManager.test_connection()` for the add-printer connection check UI flow.

---

## Testing

### Mock seam

```python
client = ElegooCentauriClient(ip_address="192.168.1.200")
client._ws = MagicMock()
```

`_ws.send` is the injection point. Set a `side_effect` that immediately resolves the pending ACK so `_send(wait_ack=True)` returns synchronously without hitting the 10-second timeout.

### ACK responder (success)

```python
def _make_ack_responder(client, ack=0):
    def _respond(raw: str) -> None:
        data = json.loads(raw)
        rid = data["Data"]["RequestID"]
        client._ack_results[rid] = ack
        event = client._pending_acks.get(rid)
        if event:
            event.set()
    return _respond

client._ws.send.side_effect = _make_ack_responder(client)
```

Pass `ack=1` to simulate a rejected command.

### Response responder (for commands that read data back)

```python
def _make_response_responder(client, ack=0, response_data=None):
    def _respond(raw: str) -> None:
        data = json.loads(raw)
        rid = data["Data"]["RequestID"]
        client._ack_results[rid] = ack
        if response_data is not None:
            client._response_data[rid] = response_data
        event = client._pending_acks.get(rid)
        if event:
            event.set()
    return _respond
```

Use for `list_files` / `list_files_by_category` tests.

### Asserting sent payload

```python
msg = json.loads(client._ws.send.call_args[0][0])
assert msg["Data"]["Cmd"] == 403
assert msg["Data"]["Data"]["LightStatus"]["SecondLight"] is True
```

### `connected_client` fixture pattern

```python
@pytest.fixture
def connected_client(client):
    client.state.connected = True
    return client
```

Tests that expect a connected printer use `connected_client`; tests that expect graceful no-op on disconnected printer use the base `client` fixture (which leaves `_ws` set but `state.connected = False`).

### Testing `_send_with_response` callers

For methods like `list_files_by_category` that call `_send_with_response` internally, replace it directly rather than using the WS mock:

```python
client._send_with_response = MagicMock(return_value=(True, {"FileList": [...]}))
```
