# Printer Interface Architecture

This is the current Themis printer-client and plugin reference. The core contract is `backend/app/services/abstract_printer_client.py`; bundled printer implementations live under `backend/app/plugins/`. For the current installed model list and runtime capability state, use the app's printer-model and capability APIs.

---

## Required neutral printer contract (architecture decision, 2026-11-21)

The neutral per-device contract is implemented in
`backend/app/plugins/printer_topology.py`. Registration rejects printer factories
without live topology refresh or an explicit print-artifact declaration. Model
metadata is not a substitute for a validated device snapshot. Printers without a
valid snapshot can connect but cannot claim or dispatch work.

A printer plugin must declare and satisfy the following through registration and
its per-device description:

* The printer has `n > 0` nozzles/toolheads.
* Each nozzle/toolhead has its own positive nozzle size. Sizes may differ; core
  must not substitute one printer-wide nozzle size for all tools.
* The printer has `m > 0` configured filament positions, with `m >= n`.
  `m` counts positions, including empty ones, not loaded spools or distinct
  material identities. A position's filament/type/subtype can change at runtime;
  an empty position remains configured but cannot satisfy a filament request.
* Filaments can have different types and subtypes.
* Equivalent filament can occupy multiple positions. Those positions retain
  separate identities; counting distinct material identities must not collapse
  them. Duplicate material does not itself prove automatic runout failover.
* If `m > 1`, the printer supports filament swapping. This covers both a shared
  nozzle changing filament and a change between tools; the mechanism belongs
  exclusively to the plugin.
* The user may disable swapping for a physical printer. That restriction does
  not remove its filament choices: it can still serve a print using any one
  available filament, but cannot serve a print requiring multiple filaments.

### Live topology refresh

Topology is per physical printer and mutable during its existing session. A
manufacturer/model registration cannot fix the actual number of positions for all
instances: for example, the owner may use a P1S with no AMS or attach up to four
AMS units. Only the Bambu plugin interprets that hardware configuration and maps
it to neutral positions; core must not calculate AMS counts or tray addresses.

Registration must declare a live-refresh operation. The live operation
is `POST /api/v1/printers/{id}/topology/refresh` through the checked resource client. It asks the bound plugin to refresh
and publish one coherent per-device snapshot containing:

* Tool identities and the current nozzle size of each tool.
* Configured filament-position identities (`m`), including empty positions.
* Each position's current contents: explicit empty state or filament identity,
  type/subtype and other declared material properties.
* The current swapping capability; the user's printer-level swapping policy
  remains separate and survives a refresh.

**Configured positions, filament contents and nozzle sizes must all refresh
without dropping/reacquiring the printer, reconnecting its client, changing its
plugin binding or deleting/recreating its saved record.** A nozzle change does
not require a new connection. The plugin owns acquisition: hardware queries,
notifications and plugin-owned user configuration where hardware cannot report a
value. Core must not guess a nozzle size or invent a loaded filament.

The contract must support an explicit user update/rescan during a session;
plugin-pushed changes use the same snapshot validation/publication path. Keep
identities stable for tools and positions that remain present. Apply a snapshot
atomically and version it so clients and scheduling never combine new slot counts
with old nozzle sizes or filament assignments. A failed/incomplete refresh is an
explicit error or stale state, not evidence that positions were removed or became
empty.

Validate `n > 0`, `m > 0`, `m >= n` and per-tool positive nozzle sizes against the
configured topology. Zero loaded spools does not invalidate that topology; it
only means no filament request can currently be satisfied. After a successful
refresh, update the UI and re-evaluate pending work and slicing eligibility using
the new snapshot. Validate the relevant snapshot/assignment again before dispatch
so a removed slot or changed nozzle cannot use an obsolete preparation. Do not
silently rewrite the tool/filament assignment of an already-running print.

Core may validate these invariants and compare neutral job requirements against
plugin declarations. Vendor slot/tray identifiers, tool-selection commands,
filament loading/swapping, runout behavior, wire protocols and hardware-specific
routing remain plugin implementation details. A plugin must either satisfy a
requested assignment or return an explicit unsupported/unavailable result;
core must not manufacture a vendor mapping or silently assume a fallback.

Everything beyond the required assumptions must be explicitly supplied through
optional capabilities or constraints. Core must not presume cameras, lights,
heated beds, chamber controls, a particular build volume, a protocol, or support
for a particular command. Registration must expose the contract and validation
hooks; a per-device description must express the actual configured tools and
filament choices without a hardcoded manufacturer table in core.

Implemented boundary changes:

1. Replace scalar toolhead/nozzle assumptions with tool identities and per-tool
   nozzle sizes, plus explicit filament-position identities and material data.
2. Express swapping capability separately from the user's printer-level policy.
   Enforce the policy by the print's filament requirements, not by hiding all but
   one loaded filament or counting duplicate positions as different materials.
3. Send neutral assignments to the selected printer plugin. Move AMS array
   construction and hardware routing validation out of the queue engine.
4. Centralize printer calls and lifecycle checks behind one gateway, with an
   explicit policy for any safety controls allowed while a plugin is disabled.
5. Move reusable protocol implementations into a plugin implementation library;
   test that core cannot import them or encode protocol payloads.
6. Validate registration and per-device descriptions, and add contract tests for
   heterogeneous nozzles, duplicate materials, `m >= n`, swapping policy and
   invalid/temporarily unavailable configurations.
7. Live topology refresh covers configured positions, live slot contents and
   per-tool nozzle sizes. Tests cover empty-to-loaded transitions, position expansion
   and removal, heterogeneous nozzle updates, stable printer/tool/position
   identity, refresh failures, stale dispatch rejection and that every refresh
   leaves the existing connection/session intact.

### Runtime use and dispatch safety

`GET /api/v1/printers/{id}` and fleet rows expose `topology`,
`topology_revision`, and `topology_error`. The refresh endpoint accepts `{}` to
rescan with saved configuration, or `{"configuration": {"nozzle_sizes_mm": [0.4,
0.6]}}` to explicitly supply sizes the hardware cannot report accurately. There
is one size per tool in the plugin's reported order. No nozzle size is guessed.
The printer edit screens provide a Refresh topology action and nozzle-size input. Fleet backup/import preserves this saved configuration; restore acquires fresh topology instead of trusting an exported snapshot or revision.

Slot contents can change without changing capacity. Manual filament edits use
the same publication path as plugin notifications. Configured positions with no
filament remain in the snapshot; hardware-reported material data retains its
authority over conflicting inventory bindings. The printer-level single-filament
policy survives refresh and does not hide other configured choices.

Refresh, queued dispatch, stored-file printing and direct upload-and-print share a per-printer lock. A pending/failed refresh blocks
starts; a completed refresh cannot clear the gate for a newer pending refresh; failure retains the last snapshot. A successful snapshot is published
atomically and versioned only when it changes, then wakes scheduling and emits
`printer_topology` so the UI refetches authoritative data. Dispatch checks the
assignment, snapshot revision, slot identity and plugin lifecycle before upload
and again before start. Cancelled or removed queue assignments never start. Direct starts validate topology before upload and again before starting, and verify the same active client session. A changed
preparation is blocked for re-evaluation, not marked as a successful print.

```mermaid
sequenceDiagram
    participant Caller as UI or plugin notification
    participant Core as Themis topology service
    participant Plugin as Bound printer plugin
    participant Device as Physical printer
    participant Queue as Scheduler / dispatch
    Caller->>Core: Refresh existing printer
    Core->>Core: Block starts; acquire per-printer lock
    Core->>Plugin: refresh_topology(saved configuration)
    Plugin->>Device: Read current topology using its protocol
    Device-->>Plugin: Hardware report
    Plugin-->>Core: Neutral tools, nozzle sizes, positions, contents
    Core->>Core: Validate; commit snapshot and revision atomically
    Core-->>Caller: Snapshot / error, retaining same session
    Core->>Queue: Wake after successful publication
    Queue->>Core: Revalidate assignment before upload and start
    Queue->>Plugin: start_print(neutral assignment)
    Plugin->>Device: Plugin-owned mapping and print command
```

Per-tool nozzle sizes reach the slicing provider and are included in cache
identity. Saved versions also retain their ordered nozzle-size vector. Explicitly selected saved slices are checked at selection and claim; mismatched or unknown legacy nozzle sizes require slicing the source model again, even if the machine profile is unchanged. The slicing plugin translates them into engine-specific settings.
Shared Moonraker/G-code/3MF implementations live under `plugins/_protocols/`;
core imports contracts, not that library. Legacy printer-type aliases are
contributed by their plugin manifests. Core no longer constructs AMS wire arrays
or contains G-code movement recipes.

The generic Klipper plugin requires real T0..Tn selection macros before claiming
multi-position swapping support. Missing firmware support produces an explicit
refresh error. The initial manufacturer implementations remain Bambu P1S,
Elegoo Centauri Carbon, and Snapmaker U1. The owner has confirmed U1 discovery and
connection; new topology reads and physical print behavior still need recorded
hardware verification. Read-only checks are in `protocol_verification/`.

---

## Overview

The system supports multiple 3D printer vendors (Bambu Lab, Elegoo, Moonraker/Klipper, Snapmaker) behind a single abstract interface. The goals are:

1. **No vendor strings in the API layer** — route handlers call `client.start_print()`, not `if bambu: … elif elegoo: …`.
2. **Capability-driven UI** — the frontend reads a capability flags object returned by each client; it never hardcodes printer type strings to decide which controls to show.
3. **Pluggable vendors** — each vendor is a plugin (`backend/app/plugins/<vendor>/`) whose manifest exposes its client class and declares the manufacturers/models it supports; the factory resolves a printer's class from its `plugin_id` at runtime, so adding a vendor requires zero changes to route or core logic.

---

## Data Structures

All structures live in `backend/app/services/abstract_printer_client.py`.

### `PrinterCapabilities` — feature flags

```python
@dataclass
class PrinterCapabilities:
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
    slot_plan: list[dict] | None = None
    loaded_filaments: list[dict] | None = None
    gcode_path: str | None = None
```

Passed to `start_print()`. Core passes the neutral selected-position plan. Plugins translate it into their commands, including AMS mappings and tool selection; vendor print flags remain plugin-owned.

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
| `jog(axis, distance_mm)` / `home_axes(axes)` | no | G-code defaults (G91/G1/G90, `G28 X Y`); gated by `axis_jog` / `home_axes` capabilities |
| `set_nozzle_temp(c)` / `set_chamber_temp(c)` | no | nozzle default `M104` when `gcode_supported`; chamber default False; gated by `nozzle_temp` / `chamber_temp` |
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

### `BambuMQTTClient` — `plugins/bambu/client.py`

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

### `ElegooCentauriClient` — `plugins/elegoo_centauri/client.py`

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

**Snapmaker** owns its plugin and subclasses the shared `MoonrakerClientBase` with U1-specific tool, filament and camera behavior. The generic Klipper plugin separately subclasses that transport. Shared transport does not share printer ownership: a physical printer has one plugin binding.

---

## The Factory

`backend/app/services/printer_client_factory.py` — resolves a printer's client class through the **plugin registry**; it imports no vendor.

A printer plugin's manifest `factory` is its client class, and its `manufacturers` list the models it supports. A `printers` row carries
`plugin_id` + `manufacturer_id` + `model_id`; old rows and backups also carry the legacy `printer_type` string, which maps onto them
(`printer_identity.LEGACY_IDENTITY`).

| Function | Purpose |
|---|---|
| `client_class(key)` | Client class for a plugin id **or** a legacy `printer_type`; `None` when no such printer plugin is registered. |
| `create_client(printer, **callbacks)` | Builds the client for an ORM `Printer` row (`plugin_id or printer_type`). Passes only the keys named in the class's `connection_fields()` from `connection_config`, plus callbacks the constructor accepts (so vendor-specific callbacks are dropped for clients that do not take them). Unknown plugin → `ValueError`. |
| `create_client_from_config(plugin_or_type, cfg)` | Same, from a config dict (test-connection, no row). |
| `printer_client_plugins(enabled_only=)` / `enabled_client_classes()` | The printer plugins / the enabled ones' classes by `printer_type` (what network discovery sweeps). |

`GET /api/v1/printers/types` returns one entry per plugin-declared model (`printer_identity.printer_model_catalog()`):
`{plugin_id, manufacturer_id, manufacturer_name, model_id, display_name, bed_mm, toolheads, connection_fields, plugin_enabled}`.
The add-printer UI offers manufacturer → model for the enabled plugins and then that plugin's `connection_fields`.

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

The manager wires the bound client's status, completion and legacy filament callbacks to neutral internal events.
Live topology notifications use the same validated publication service as the refresh endpoint.

| Callback | Signature | Trigger |
|----------|-----------|---------|
| `on_state_change` | `(printer_id, state)` | Normalized printer status |
| `on_print_complete` | `(printer_id, state)` | Print completion |
| `on_filament_change` | `(printer_id, slots)` | Legacy material reports before a full topology exists |
| `on_topology_change` | `(printer_id)` | Query and publish the current complete topology |

`connect_printer` wires callbacks on the existing plugin client; refreshing its topology does not reconnect it.

---

## State Serialization

Each vendor has a vendor-specific state dataclass (`PrinterState`, `ElegooState`, `MoonrakerState`). The API and WebSocket push need a **normalized JSON structure** that the frontend can consume uniformly.

### `AbstractPrinterClient.serialize_state(printer_id)`

Each client returns its own normalized dict (the vendors implement it as a module-level `serialize_<vendor>(state, printer_id)` plus a
one-line method; the base-class default is `{id, printer_type, connected}`). `PrinterManager.get_normalized_state` calls it and overlays
`connected`, `capabilities` and `awaiting_plate_clear`. There is no per-type table and no `isinstance` in core.

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

1. Create the plugin package `backend/app/plugins/<vendor>/` with `client.py` (the client), optional `alarms.py`, `__init__.py` (`MANIFEST`) and `themis-plugin.toml`.
2. Define a vendor state dataclass (add `raw_data → None` and `state → print_state` compat properties if needed).
3. Subclass `AbstractPrinterClient`; set `printer_type = "<vendor>"`.
4. Override `connection_fields()` with the required credentials.
5. Implement all `@abstractmethod` methods; override optional methods for supported features (`set_chamber_light`, `upload_file`, …).
6. Override `get_capabilities()`, and `is_idle` / `is_printing` where your state enum has clean mappings.
7. Implement `serialize_state(printer_id)` (the normalized status dict).
8. Optional: `discover_host` / `parse_announcement` / `SSDP_PORTS` for discovery; `slice_tool_mapping = True` for tool-changer printers; `get_alarms()` backed by your `alarms.py`.
9. In `__init__.py` export `MANIFEST = PluginManifest(id=…, factory=<ClientClass>, settings_model=<empty model>, manufacturers=(…,), default_enabled=…)`.
10. Nothing else: no core, route or frontend change (see `docs/agent/recipes.md` § Add a printer vendor).

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


## Filament usage reports (BIZ-366, contract v2)

**The printer plugin owns usage reporting.** For each physical print attempt it tells Themis how many grams each of its loaded slots
fed. Themis owns which spool was in each slot, so it never infers rollover: an AMS rollover onto a backup spool is simply a second
slot in the report. Call `await ctx.job_reporter.report_usage(job_id, report, printer_id=printer_id)` while the job is printing.
The report is a **cumulative** snapshot for the attempt, never an incremental debit:

```json
{
  "version": 2,
  "attempt_id": "the job's filament_usage_context.attempt_id",
  "revision": 3,
  "final": true,
  "source": "reported",
  "slots": [{"slot": 9, "grams": 5.0}, {"slot": 4, "grams": 15.0}]
}
```

- **`attempt_id`:** read it from `ctx.job_details.get(job_id, ["filament_usage_context"])`. A new attempt id starts every time the
  job starts printing, including a reprint after a failure.
- **`slot`:** your published slot id, the `slot` of an entry in the printer's `loaded_filaments` (a Bambu global tray id). It is
  never a list position or a spool ref.
- **Slot entries:** at most 64, each slot id once, grams finite and ≥ 0.
- **`source`:** `reported` (the printer's own figure) or `measured` (verified device data only).
- **`revision`:** increases within an attempt. A duplicate or older revision returns `False`. Errors (`JobReportError`):
  - a report for another attempt;
  - a malformed report;
  - a report sent after the attempt ended.

  Send your last report **before** reporting `complete` or `failed`. A **cancelled** print (job cancel, or a stop from Themis) is
  settled 120 s after the cancel (`completion_events.CANCEL_SETTLE_GRACE_S`): until then its attempt still takes your final report,
  sent once the printer has actually stopped.

**Every attempt is settled when it ends:**
- **How an attempt ends:** completed (`job.complete`), failed, cancelled while printing, or requeued after the printer died (all
  three via the durable `job.attempt_ended`).
- **Spool binding:** each reported slot is charged to the spool bound to that slot when the attempt started. A slot that was empty
  then, such as a spool loaded mid-print, uses its binding when the attempt ended.
- **No report:** a completed attempt falls back to the slicer estimate routed through the claim-time slot plan, labelled
  `estimated`. A failed, cancelled or requeued attempt deducts nothing and the job notes "no usage reported".
- **Reprints add up:** each attempt deducts on its own (start snapshots and inventory writes are per attempt). The job's
  `filament_accounting` lists every attempt and `total_grams` is their sum (`jobs.actual_filament_grams` stays the sliced estimate).
- **Frozen once settled:** an attempt's allocation is frozen when it is first settled. A redelivered event, or a revision sent
  after the attempt ended, never deducts again or differently.

Never invent sensor data or infer grams from print progress: report what the printer exposes, or nothing (the estimate then applies to
completed prints). Which printers report what is per plugin; Bambu's per-tray reporting, rollover included, is BIZ-367.

Slot plans record stable position and material identity at claim. Dispatch rejects an assignment if that identity or the
topology revision changes. Bambu's plugin alone builds its AMS wire array from those neutral positions; core never encodes tray ids.
