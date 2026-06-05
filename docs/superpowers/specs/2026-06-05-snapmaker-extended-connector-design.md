# Snapmaker U1 "Extended" Connector — Design Spec (Project 1)

**Date:** 2026-06-05
**Status:** Approved (pending spec review)
**Branch:** `snapmaker-u1`

## Goal & scope

Add a new printer vendor connector, **`snapmaker_extended`**, so a Snapmaker U1 running the
"SnapmakerU1-Extended-Firmware" (Klipper + Moonraker) can be added, monitored, sliced for, and printed
to from Themis. It is one `AbstractPrinterClient` subclass + one registry entry; it flows into the
add-printer wizard, Fleet, queue gating, and slicing automatically (the `printer-interface` pattern).

**In scope (Project 1):** connect + live status (WebSocket), print control (upload gcode + start /
pause / resume / cancel), `send_gcode`, snapshot camera, 4 **manual** filament slots, single-material
printing end-to-end through the existing slice → upload → print pipeline.

**Out of scope (Project 2, separate design):** model-filament → printer-tool mapping and per-job tool
selection ("print on tool 2 vs 3"). That touches the shared job model + new-job UI and needs hands-on
verification of how OrcaSlicer + the Extended firmware target a specific extruder. Project 1 is built
**multi-tool-aware** (4 slots, per-extruder temps, and it just uploads whatever gcode the slicer emits,
including tool-change gcode) so Project 2 needs no connector rework.

## Verified protocol (live probe of the real printer, 2026-06-05)

- **Moonraker 1.1.0**, API 1.4.0, on **:7125**, open on the LAN (`/access/info` → `login_required:
  false`, `trusted: true`). Web UI (Fluidd/Mainsail) on :80.
- **WebSocket JSON-RPC** at `ws://<ip>:7125/websocket` (same path the Elegoo client already uses).
- **4 extruders** (`extruder`, `extruder1`, `extruder2`, `extruder3`); `heater_bed`; `toolhead`
  (`axis_maximum` ≈ 271×335×275). Feed modules: `filament_feed left` (ext0/1), `right` (ext2/3).
- **`print_stats.state`**: `standby` | `printing` | `paused` | `complete` | `cancelled` | `error`;
  plus `filename`, `print_duration`, `filament_used`, `info.current_layer`/`total_layer`.
  `display_status.progress` (0..1).
- **Camera**: webcam `case`, snapshot at `http://<ip>/webcam/snapshot.jpg` (WebRTC stream skipped).
- **Slicing**: Klipper ingests plain **gcode** (no 3MF) → default `orca_export_args` ([]).

## Architecture

Mirror the existing **Elegoo Centauri client** (`elegoo_centauri_client.py`), which already uses the
exact stack we need: `websocket.WebSocketApp` (the `websocket-client` dep) in a background thread for
push status + `httpx` for HTTP. No new dependencies.

### New file: `backend/app/services/snapmaker_client.py`

```python
class SnapmakerExtendedClient(AbstractPrinterClient):
    printer_type: ClassVar[str] = "snapmaker_extended"
```

#### State dataclass
`SnapmakerState` (dataclass): `connected: bool`, `state: str` ("unknown"→normalized), `current_print:
str|None`, `progress: float`, `print_duration: float`, `layer_num: int`, `total_layers: int`,
`temperatures: dict` (`{"nozzle": <active extruder temp>, "nozzle_targets": [t0..t3], "bed", "bed_target",
"chamber"?}` — see serialization), `extruders: list[dict]` (per-tool `{index, temp, target}`),
`klippy_ready: bool`, `raw_data: dict`.

#### connection_fields (classmethod)
```
ip_address   (text, required, placeholder "192.168.0.x")
port         (number, default 7125, required=False, help: "Moonraker port")
api_key      (password, required=False, help: "Only if Moonraker requires an API key; blank for an
              open LAN printer.")
```

#### Connection lifecycle (must-implement)
- `connect(loop=None)`: store `self._loop`; spawn a background thread running a `websocket.WebSocketApp`
  on `ws://{ip}:{port}/websocket` with `run_forever` + reconnect (copy Elegoo's thread + `_RECONNECT_DELAY`
  pattern). `on_open` → send JSON-RPC `printer.objects.subscribe` for the objects we read (below) and a
  one-shot `server.info`/`printer.info` to learn `klippy_state`. `on_message` → dispatch JSON-RPC
  results + `notify_status_update` / `notify_klippy_ready` / `notify_klippy_disconnected` /
  `notify_klippy_shutdown`. If `api_key` is set, send it via the `X-Api-Key` header on the WS handshake
  and on all httpx calls. **Log** connect success and every failure (rc/exception) — apply the Bambu
  lesson; do not fail silently.
- `connected` property → `self.state.connected` (true only when WS open AND `klippy_ready`).
- `disconnect(timeout=0)`: close the WS, stop the thread, set `connected=False`.
- `check_staleness()`: if `now - last_message_time > STALE_TIMEOUT` and past the reconnect cooldown,
  force a reconnect (copy Elegoo).

**Subscribed objects** (`printer.objects.subscribe` params + the initial `printer.objects.query`):
`print_stats`, `display_status`, `heater_bed`, `extruder`, `extruder1`, `extruder2`, `extruder3`,
`toolhead`. Map `notify_status_update` deltas onto `SnapmakerState`; schedule the manager's
`_on_state_change` callback on `self._loop` via `run_coroutine_threadsafe` (Elegoo/Bambu pattern). When
`print_stats.state` transitions to `complete`, fire `_on_print_complete`.

#### Print control (must-implement) — Moonraker HTTP via httpx
- `file_upload_supported` → `True`. `upload_file(data: bytes, filename: str) -> bool`: `POST
  http://{ip}:{port}/server/files/upload` (multipart, field `file`, `root=gcodes`) → returns ok.
- `start_print(file_name, options=None) -> bool`: `POST /printer/print/start` with `?filename=<name>`
  (the gcode already uploaded). (`StartPrintOptions.gcode_path` / target tool are ignored in v1 — the
  default tool is whatever the sliced gcode targets; tool selection is Project 2.)
- `stop_print` → `POST /printer/print/cancel`. `pause_print` → `/printer/print/pause`. `resume_print`
  → `/printer/print/resume`.
- `send_gcode(gcode) -> bool`: `POST /printer/gcode/script?script=<gcode>`. (Enables the base
  `home`/`jog_z`; `set_bed_temp` → `send_gcode("M140 S{c}")`.)
- `request_status_update()`: one-shot `printer.objects.query` over the WS (no-op-safe; the subscription
  already pushes).

#### Capabilities & lifecycle hooks (override)
- `get_capabilities()` → `PrinterCapabilities(pause_resume=True, gcode=True, camera=True)`. (No
  AMS/chamber-light in v1.)
- `is_idle` → `state.state == "IDLE"` (normalized from `standby`/`complete`/`cancelled`).
  `is_printing` → `state.state in ("RUNNING", "PAUSE")`.
- `orca_export_args` → **inherit default** ([] = raw gcode). No override.
- `get_loaded_filaments()` → `[]`. The 4 slots are **manual** (DB-stored, user-set in Edit); the client
  does not auto-report them, so there is **no `_on_ams_change`** wiring for v1. Filament gating uses the
  printer's DB `loaded_filaments` as today.
- `control_endpoint()` → `(self._ip, self._port)` so the add-printer "test connection" gives a useful
  reachability reason (uses the diagnostics added for Bambu).
- `camera_mjpeg_url` → `http://{ip}/webcam/stream` (camera-streamer MJPEG; best-effort) **and** the
  snapshot path `http://{ip}/webcam/snapshot.jpg` is what the snapshot route grabs. (Verify which the
  camera proxy consumes; snapshot is the must-have, stream is best-effort.)

### Status normalization: `printer_manager._serialize_snapmaker`
Add `_serialize_snapmaker(state, printer_id)` and register it in `_STATUS_SERIALIZERS["snapmaker_extended"]`.
Emit the same normalized shape the Fleet card consumes: `printer_type`, `id`, `connected`, `state`,
`current_print`, `progress`, `remaining_time` (derive from `print_duration` + estimate if available, else
0), `layer_num`, `total_layers`, `temperatures` (`nozzle`/`bed` of the **active** extruder for the card;
include `extruders` array for per-tool detail), plus capability flags via `get_normalized_state`'s
generic merge. Reuse the Bambu serializer as the template.

State string mapping (Klipper `print_stats.state` → Themis normalized). The app's normalized
vocabulary is **IDLE / RUNNING / PAUSE / FINISH / FAILED** only (matches Bambu/Elegoo; the frontend
`fleet.ts` maps `FAILED → error` and has no `CANCELLED`/`ERROR` case):
`standby → IDLE`, `printing → RUNNING`, `paused → PAUSE`, `complete → FINISH`, `cancelled → FAILED`,
`error → FAILED`. `is_idle` checks the raw `print_state` (`standby`/`complete`/`cancelled`).

### Registry: `printer_client_factory.py`
```python
REGISTRY = {
    "bambu": "...",
    "elegoo_centauri": "...",
    "snapmaker_extended": "app.services.snapmaker_client.SnapmakerExtendedClient",
}
_DISPLAY_NAMES = { ..., "snapmaker_extended": "Snapmaker U1 (Extended)" }
```
Nothing else changes: the wizard reads `connection_fields()`, Fleet reads the serializer, queue/slicing
use the generic path (machine preset → gcode → upload → start).

## Slicing
No special handling. The user picks the Snapmaker U1 OrcaSlicer **machine profile** in the add/edit flow
(make/model picker → `current_orca_printer_profile`); the queue slices with that profile + the matched
filament profile, OrcaSlicer writes raw gcode (default export args), the connector uploads it and starts
the print. Multi-tool gcode (tool changes baked by OrcaSlicer) prints as-is. The model→tool mapping UI is
Project 2.

## Error handling
- All Moonraker HTTP calls wrapped; on failure log + return `False` (don't crash the queue). `start_print`
  uploads then starts; if upload fails, return False (the queue's slice-failure/retry path handles it).
- WebSocket drop → reconnect with backoff (Elegoo pattern); `connected` flips false so the printer leaves
  the ready set until back.
- `api_key` optional: if Moonraker returns 401, surface it (the test-connection hint already classifies
  "reached but login failed").
- Connect/disconnect/refusal **logged** (the app now has logging config from the Bambu fix).

## Testing
**Backend (pytest, mirror `test_elegoo_centauri_client.py` / `test_bambu_mqtt.py`):**
- `connection_fields()` returns ip_address/port/api_key with correct types/defaults.
- `_handle_message` / status mapping: feed a `notify_status_update` payload (`print_stats.state=printing`,
  extruder/bed temps, `display_status.progress`) → `SnapmakerState` updates; `is_printing` true; a
  `state=complete` payload → `is_idle` true and `_on_print_complete` fires.
- State string mapping table (standby/printing/paused/complete/cancelled/error → normalized).
- `start_print` / `pause` / `resume` / `stop` / `send_gcode` issue the right Moonraker HTTP calls (mock
  httpx; assert URL + method + params). `upload_file` posts multipart to `/server/files/upload`.
- `printer_manager._serialize_snapmaker` produces the normalized dict (registered in `_STATUS_SERIALIZERS`).
- Registry: `create_client_from_config("snapmaker_extended", {...})` builds the client; `get_printer_types_for_ui`
  includes it with the display name.
- `control_endpoint()` returns `(ip, port)`.

(No frontend code changes — the wizard/Fleet/Edit are vendor-agnostic and already render from
`connection_fields()` + the serializer + `loaded_filaments`.)

## File structure
**Create:** `backend/app/services/snapmaker_client.py`,
`backend/tests/services/test_snapmaker_client.py`.
**Modify:** `backend/app/services/printer_manager.py` (`_serialize_snapmaker` + `_STATUS_SERIALIZERS`
entry); `backend/app/services/printer_client_factory.py` (`REGISTRY` + `_DISPLAY_NAMES`).
**Docs:** update `docs/agent/printers.md` (new vendor section) via `themis-docs-sync` after implementation.

## Verification against the real printer (post-build)
Add the printer in the running app (IP `192.168.0.119` from `.env`, port 7125, no api key); confirm the
Fleet card shows connected + live temps + state; slice + print a small single-material model and confirm
upload + start + progress + completion. A `scripts/snapmaker_smoke_test.py` (reads `SNAPMAKER_IP`) may be
added for a quick Moonraker connectivity check, like the Bambu one.
