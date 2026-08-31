# Printer Integration & Protocols

The vendor-abstraction is the most-extended part of the codebase. Adding a printer = one client
class + one registry entry; everything downstream (fleet, queue, slicing dispatch) is vendor-agnostic.

## The contract: `AbstractPrinterClient` (`services/abstract_printer_client.py`)

**Must implement (abstract):** `connected` (prop), `connect(loop)`, `disconnect(timeout)`,
`check_staleness()`, `start_print(file_name, options)`, `stop_print()`, `pause_print()`,
`resume_print()`, `send_gcode(gcode)`, `request_status_update()`.

**Override as needed (have defaults):** `connection_fields()` (classmethod → add-printer form fields
+ ctor kwargs the factory passes), `get_capabilities()` (`PrinterCapabilities` flags drive UI controls),
`is_idle`/`is_printing` (props), `file_upload_supported`, `upload_file(data, filename)`,
`orca_export_args(file_base)` (slicing artifact — `[]` = raw `.gcode`; Bambu = `["--export-3mf", f"{base}.gcode.3mf"]`),
`get_loaded_filaments()` (AMS), `camera_rtsp_url`/`camera_mjpeg_url`, `home`/`jog_z`/`set_bed_temp`/
`set_fan_speeds`/`set_chamber_light`, `printer_type` (ClassVar registry key).

**Callbacks** (set by `printer_manager.connect_printer`, fired from the client's bg thread via
`run_coroutine_threadsafe(self._loop)`): `_on_state_change(state)`, `_on_print_complete(state)`,
`_on_ams_change(trays)` (only wired if the client has the attr).

**`StartPrintOptions`**: `plate_id, gcode_path, ams_mapping?, bed_levelling, flow_cali, vibration_cali,
layer_inspect, timelapse, use_ams`. The queue engine fills `plate_id`/`gcode_path`/`ams_mapping`;
**per-printer flags (leveling/timelapse/etc.) are taken from the client's own config, not from options**
(options always present, so vendor `start_print` reads `self._*`).

## Registry (`services/printer_client_factory.py`)

`REGISTRY = {"bambu": "...BambuMQTTClient", "elegoo_centauri": "...ElegooCentauriClient",
"snapmaker_extended": "...SnapmakerExtendedClient"}` + a display-name map (`"snapmaker_extended"` →
"Snapmaker U1 (Extended)"). `create_client(printer)`/`create_client_from_config(type, cfg)` pass only
`connection_fields()` names from `connection_config` into the ctor (+ callbacks if the ctor accepts them).
Add a vendor → add both entries; everything else auto-wires.

## Status serialization (`printer_manager._STATUS_SERIALIZERS`)

`type → fn(state, id) -> normalized dict` consumed by `fleet.py` and the WS `printer_state` broadcast.
Normalized keys: `state, current_print, progress, remaining_time, layer_num, total_layers,
temperatures, fan_model/aux/box, speed_factor, klippy_state, cover_url`. A new vendor needs a serializer
entry. `loaded_filaments`/`awaiting_plate_clear`/`queue_on`/`enabled` come from the DB row in `fleet.py`,
not the serializer.

## Slicing pipeline (`SlicerService` → Laminus sidecar)

**As of the 2026-06-23 Orca-sidecar migration, Themis does not invoke OrcaSlicer locally for
production slicing.** `SlicerService.slice(SliceRequest)` resolves `machine_preset`/`process_preset`/
`filament_presets` names to UUIDs against the sidecar's cached profile catalog, then delegates the
whole slice — 3MF assembly, profile resolution, gcode generation — to a separate Laminus process over
HTTP (`laminus_sidecar_client.LaminusSidecarClient.slice_start` → `poll_status` → `download`). The
pre-sidecar local pipeline (`preset_resolver.py`, `profile_index.py`, `project_config_builder.py`, and
most of `mesh_3mf_builder.py`) has no remaining callers — see `backend.md` § Services for what's dead.

**`SliceRequest.prepare_hook`** (opaque `Callable[[Path], None]`, bound by
`queue_engine._run_slice_and_print` to `client.remap_sliceable_3mf`) still runs — but now against a
**job-scoped copy** of the source 3MF that `SlicerService` makes in the job's output directory, not the
original. `source_3mf` is the shared library file every job/printer referencing that upload resolves
to; running the hook on it directly would mutate it for every other consumer (this was a real
regression, fixed — see the git log around `slicer_service.py`). Recovery tier: on `SliceError`, the
sidecar itself retries geometry-only; `prepare_hook` isn't Themis's to re-invoke on that retry.
`filament_profile` (from the matched loaded slot) is passed as the filament preset; `filament_colours`
from the job ask.

**`mesh_3mf_builder`'s vendor-agnostic design carries over conceptually** even though its actual
3MF-building functions are now dead: `tool_index`/`filament_map` were never builder params — all
vendor-specific routing is delegated to the printer client's `remap_sliceable_3mf` hook, which still
holds under the sidecar architecture (it's applied to the job-scoped copy described above, same as
before).

**Filament→tool routing is a vendor operation** (`AbstractPrinterClient.remap_sliceable_3mf`):
- **Default (no-op):** vendors that realize the mapping elsewhere (Bambu: at print time via
  `ams_mapping`) inherit the base class default and return immediately.
- **`SnapmakerExtendedClient` override:** delegates to `services/snapmaker/remap.remap_3mf(prepared,
  *, tool_index, filament_map)` — rewrites the prepared 3MF in-place:
  - *Single-extruder* (`tool_index` set): per-object `extruder` metadata in
    `Metadata/model_settings.config` (`<metadata key="extruder" value="{tool_index+1}"/>`,
    1-based), all objects assigned to that tool.
  - *Multi-material* (`filament_map` non-empty list of `{model_filament(1-based), tool_index(0-based)}`):
    1. **`paint_color` rewrite** (nibble-packed TriangleSelector codec in
       `services/snapmaker/paint_remap.remap_paint_color(hex, mapping)`) — swaps every filament
       leaf state (`state=filament+2` → `state=tool_index+3`); byte-exact round-trip.
       Codec: nibbles right-to-left; bits LSB-first per nibble; 2-bit `split_sides`; leaf
       `code==3` → 4-bit nibble for states ≥ 3. *AGPL-sensitive — isolated here to avoid
       licensing contamination of the generic slicer path.*
    2. **Object `extruder` metadata** patch in `model_settings.config` per the map.
    - Plate `filament_maps` in `project_settings.config` is **left untouched** — OrcaSlicer
      ignores it for CLI slicing (spike-proven); `paint_color` + `extruder` metadata is authoritative.
  - `tool_index` and `filament_map` are mutually exclusive.
- Queue binds the hook: `prepare_hook = lambda p: client.remap_sliceable_3mf(p, tool_index=ti,
  filament_map=fm)` when either is set; otherwise `prepare_hook=None`.

**Filament slot resolution** (unchanged): `_slot_for_config(config, loaded)` uses
`loaded[tool_index]` when `tool_index` is set; else `_matching_loaded_filament` (type+color ask).
`_filament_mismatch` gates eligibility. Multi-material: `_mapped_tools_loaded(fmap, loaded)` checks
every mapped `tool_index` has a loaded filament; queue passes N `filament_presets` (one per extruder,
ordered by tool) into `SliceRequest` when `filament_map` is set.

## Filament gating & AMS

Queue claim matches the job's **ask** (`config.filament_type`+`filament_color`) against the printer's
`loaded_filaments` (`_matching_loaded_filament`, type+color, case/`#`-insensitive; empty ask ⇒ first
slot). The matched slot supplies `filament_profile` (orca preset for slicing) and, for AMS,
`ams_tray_id` → `StartPrintOptions.ams_mapping=[id]`. Mismatch ⇒ job **blocked** (transient).

**AMS auto-sync merge** (`printer_manager.on_ams_change`): when the Bambu client fires `_on_ams_change`
with fresh tray dicts, `on_ams_change` merges rather than overwrites — the incoming trays are joined to
the existing `loaded_filaments` by `slot`; each matched slot's `filament_profile` and `spoolman_spool_id`
are preserved from the previous DB value. Slots no longer reported in the AMS payload are dropped along
with their mappings. `filament_id` in a tray dict carries the Bambu AMS material code (e.g. `"GFL99"`)
and is never repurposed for Spoolman.

## Vendor specifics

### Elegoo Centauri (`elegoo_centauri_client.py`) — SDCP
WebSocket `ws://<ip>:3030/websocket`; numeric `Cmd` IDs. Upload = single multipart **POST**
`http://<ip>:3030/uploadFile/upload` (`TotalSize/Uuid/Offset:0/Check:1/S-File-MD5` + file). `start_print`
(Cmd 128) needs the **`/local/<file>` path + params** `{StartLayer:0, Calibration_switch, PrintPlatformType,
Tlp_Switch}` — a bare filename is acked but won't start. **Ack quirk**: print-control results nest as
`Data.Data.Ack`; `_parse_response_msg` falls back to it (top-level `Result`/`Ack` for others). `stop`
is acked but **deferred during bed-flatness calibration** (lands after). Per-printer config:
`bed_type, bed_leveling, timelapse`. Full notes: `docs/elegoo-centauri-client.md`.

### Bambu (`bambu_mqtt.py`) — MQTT + FTPS
MQTT TLS `:8883` (user `bblp`, pw = access code, `tls_insecure`), topics `device/<serial>/request|report`.
Upload = **implicit FTPS** `:990` (`_ImplicitFTP_TLS`, `prot_p()`, self-signed) — NOT plain FTP.
`start_print` = `project_file` command referencing the uploaded `.gcode.3mf` (param
`Metadata/plate_N.gcode`). **AMS**: `_parse_ams` flattens `print.ams.ams[].tray[]` + external `vt_tray`
into loaded-filament dicts (global tray id = unit*4+tray, external=254; color = 8-hex RGBA→`#RRGGBB`;
skip empty). `on_ams_change` auto-syncs trays → DB `loaded_filaments`. `start_print` sends `ams_mapping`
(from the matched tray) + per-printer flags `use_ams/bed_leveling/flow_cali/timelapse`. Per-printer
config = those flags. Status: `gcode_state` (IDLE/RUNNING/PAUSE/FINISH/FAILED), `stg_cur`, fans (0–15
gears → %). Camera: X1 = RTSP `:322`; **P1/A1 differ** (chamber image `:6000`, not yet handled).
**Validation status**: built + unit-tested; live hardware validation (FTPS reachability, real AMS field
names, test print with mapping) pending.

### Snapmaker U1 Extended (`snapmaker_client.py`) — Moonraker/Klipper
Custom Klipper firmware ("SnapmakerU1-Extended"). **Status** streams over the Moonraker WebSocket
`ws://<ip>:<port>/websocket` (JSON-RPC; default port **7125**): `_on_ws_open` sends `server.info` +
`printer.objects.subscribe` + `printer.objects.query` for `print_stats, display_status, heater_bed,
extruder, extruder1, extruder2, extruder3, toolhead`; `notify_status_update` deltas → `_apply_status`
(per-field merge, since Moonraker sends partial diffs); `notify_klippy_ready/disconnected/shutdown` set
`klippy_ready`. `connected` = WS open **AND** `klippy_ready`. **Control** is Moonraker **HTTP** via
`httpx`: `upload_file` = multipart **POST** `/server/files/upload` (`root=gcodes`); `start_print` →
`POST /printer/print/start?filename=`; pause/resume/cancel → `/printer/print/{pause,resume,cancel}`;
`send_gcode` → `POST /printer/gcode/script?script=` (enables `home`/`jog_z`/`set_bed_temp`=`M140`).
Optional `api_key` → `X-Api-Key` header on the WS handshake + every httpx call (blank for an open LAN
printer). `connection_fields` = `ip_address, port (7125), api_key`. Reconnect: bg-thread `run_forever`
loop + `check_staleness` closes the WS to force reconnect on silence. RPC ids via `itertools.count`
(atomic — `request_status_update` is called from the asyncio thread). **Slicing**: default
`orca_export_args` (`[]` = raw `.gcode`; Klipper ingests plain gcode). **State** map (`_NORM_STATE`):
`standby→IDLE, printing→RUNNING, paused→PAUSE, complete→FINISH, cancelled→FAILED, error→FAILED` (app
vocab is IDLE/RUNNING/PAUSE/FINISH/FAILED only). **Filaments**: 4 **manual** slots (slot 0-3 ↔
extruder0-3); `get_loaded_filaments` is unused — slots are user-set in the DB `loaded_filaments`, no
`_on_ams_change` auto-sync. **Camera**: snapshot `http://<ip>/webcam/snapshot.jpg`; `camera_mjpeg_url` =
`/webcam/stream` (best-effort). Per-tool temps in `state.temperatures["extruders"]`.
**Single-filament tool pick** (Project 2 — delivered): user selects a tool (T0–T3) per printer in
`NewJobScreen`; persisted as `job_printer_configs.tool_index`; queue routes via `_slot_for_config` and
applies the routing via `remap_sliceable_3mf` → `snapmaker/remap.remap_3mf` (see Slicing pipeline above).
**Multi-material model→tool mapping** (Project 2b — delivered): `job_printer_configs.filament_map`
maps each declared model filament to a physical tool; routing applied via `remap_sliceable_3mf` →
`snapmaker/remap.remap_3mf` (rewrites `paint_color` bitstreams + object `extruder` metadata; see Slicing pipeline above).
**Validation status**: built + unit-tested; live Moonraker connectivity confirmed
(`scripts/snapmaker_smoke_test.py`, reads `SNAPMAKER_IP`); test print pending.
