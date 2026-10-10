# Printer Integration & Protocols

The vendor-abstraction is the most-extended part of the codebase. Adding a printer vendor = one **plugin package**
`app/plugins/<vendor>/` (client class in `client.py`, optional `alarms.py`, a `MANIFEST` declaring its manufacturers/models +
`themis-plugin.toml`); everything downstream (fleet, queue, slicing dispatch) is vendor-agnostic and names no vendor.

## The contract: `AbstractPrinterClient` (`services/abstract_printer_client.py`)

**Must implement (abstract):** `connected` (prop), `connect(loop)`, `disconnect(timeout)`,
`check_staleness()`, `start_print(file_name, options)`, `stop_print()`, `pause_print()`,
`resume_print()`, `send_gcode(gcode)`, `request_status_update()`.

**Override as needed (have defaults):** `connection_fields()` (classmethod → add-printer form fields
+ ctor kwargs the factory passes), `get_capabilities()` (`PrinterCapabilities` flags drive UI controls),
`is_idle`/`is_printing` (props), `file_upload_supported`, `upload_file(data, filename)`,
`orca_export_args(file_base)` (slicing artifact — `[]` = raw `.gcode`; Bambu = `["--export-3mf", f"{base}.gcode.3mf"]`),
`raw_gcode_supported` / `sliced_archive_supported` (which pre-sliced library files the vendor prints as-is — `.gcode` /
`.gcode.3mf`; Bambu = False / True, others True / False),
`get_loaded_filaments()` (AMS), `camera_rtsp_url`/`camera_mjpeg_url`, `home`/`jog_z`/`set_bed_temp`/`list_files`/`delete_file`/`download_file` (capabilities `file_browser`/`file_delete`/`file_download`; Bambu = FTPS `LIST`/`DELE`/`RETR` over root + `/cache`, Snapmaker = Moonraker `/server/files/directory?extended=true` + `/server/files/gcodes/<path>` with inline metadata, Elegoo = SDCP list + delete only; ids are vendor paths — `PrinterFile.id` — handed back verbatim; verified against virtual printers in CI and real ones via `backend/protocol_verification`)/`jog`/`home_axes`/`set_nozzle_temp`/`set_chamber_temp` (console capabilities `axis_jog`/`home_axes`/`nozzle_temp`/`chamber_temp`/`direct_upload`; Bambu + Snapmaker via G-code, Elegoo only `direct_upload` until its SDCP axis/setpoint commands are verified on hardware)/
`set_fan_speeds`/`set_chamber_light`, `printer_type` (ClassVar, the legacy key rows/backups/discovery still carry),
`serialize_state(printer_id)` (the vendor's normalized status dict — see below; base default = identity only),
`camera_configured` (default: a camera URL is stored; override when the feed isn't URL-based) / `camera_stream()` / `camera_snapshot()` / `camera_unavailable_reason()` (the client produces its own camera feed for the core camera hub; defaults proxy `camera_mjpeg_url`; Bambu transcodes RTSP in `plugins/bambu/camera.py`),
`slice_tool_mapping` (ClassVar bool, default False; True = the 3MF's filament→tool routing is baked in at slice time — Snapmaker),
`SSDP_PORTS` (optional ClassVar tuple; discovery listens for announcements on these — Bambu). The camera routes only serve a client whose `camera_configured` is true (404 otherwise; default = a camera URL is stored); the feed itself comes from the client.

**Callbacks** (set by `printer_manager.connect_printer`, fired from the client's bg thread via
`run_coroutine_threadsafe(self._loop)`): `_on_state_change(state)`, `_on_print_complete(state)`,
`_on_ams_change(trays)` (only wired if the client has the attr). They no longer call the manager directly: each publishes a typed
event on the process bus (`services/events.event_bus` → `PrinterStateChanged` / `PrintCompleted` / `AmsChanged` / `AlarmsReported` (the manager publishes the client's `get_alarms()` on each state change; its handler `on_alarms` feeds the alarm tracker) in
`services/printer_events.py`), and `PrinterManager.subscribe_events()` (called once in the `main.py` lifespan, idempotent) routes them
to `on_state_change` / `on_print_complete` / `on_ams_change`. Handlers are isolated tasks; publishers never wait. Each is bounded by the bus's `handler_timeout` (10 s) except the print-completion handler, subscribed with `timeout=None` because completing a job (commit, inventory deduction, webhooks) must never be cut short.

**`StartPrintOptions`**: `plate_id, gcode_path, ams_mapping?, bed_levelling, flow_cali, vibration_cali,
layer_inspect, timelapse, use_ams`. The queue engine fills `plate_id`/`gcode_path`/`ams_mapping`;
**per-printer flags (leveling/timelapse/etc.) are taken from the client's own config, not from options**
(options always present, so vendor `start_print` reads `self._*`).

## Plugins & resolution (`services/printer_client_factory.py`, `services/printer_identity.py`)

A printer plugin's `MANIFEST.factory` **is the client class** (never instantiated by the host — a client is built per printer row) and
its `manufacturers` declare the models it supports (`Manufacturer(id, name, models=(PrinterModel(id, name, bed_mm, toolheads),))`;
ids unique per plugin). Bundled: `app/plugins/{bambu,elegoo_centauri,snapmaker,mock}/`. `default_enabled=True` for the first three (so
existing printers are not dormant after upgrade); `mock` is enabled only when `THEMIS_MOCK_PRINTERS` is set (tests/dev).

`printers` rows carry `plugin_id` + `manufacturer_id` + `model_id` (+ `model_uuid`, the core registry's stable id — `services/printer_model_registry.py`, `GET/PATCH /printer-models`; see data-model.md) (v042 backfilled from the legacy `printer_type`:
`bambu`→bambu/bambu/p1s, `elegoo_centauri`→elegoo_centauri/elegoo/centauri, `snapmaker_extended`→snapmaker/snapmaker/u1_extended,
`mock`→mock/mock/mock; the Bambu plugin offers every Bambu model). `printer_identity.LEGACY_IDENTITY` is that mapping. A printer created from an identity triple stores its client's own `printer_type` (e.g. `snapmaker_extended` for plugin `snapmaker`) so badges and old consumers keyed on it keep working; creating one on a **disabled** plugin is a 422. Fleet backup carries the triple; import keeps a backup's triple verbatim (an uninstalled plugin ⇒ imported dormant) and maps an old backup's `printer_type` through `LEGACY_IDENTITY`.

- `client_class(key)` — client class for a plugin id **or** a legacy `printer_type`; None if no such printer plugin.
  `create_client(printer)` uses `printer.plugin_id or printer.printer_type`; `create_client_from_config(plugin_or_type, cfg)`; both pass only
  `connection_fields()` names to the ctor (+ callbacks if the ctor accepts them); unknown → `ValueError`.
- `printer_client_plugins(enabled_only=)`, `enabled_client_classes()` (what discovery sweeps), `printer_type_names()`, `printer_type_plugins()`.
- `printer_identity`: `declared_model(plugin_id, mfr, model)` (raises `IdentityError`), `resolve_legacy(printer_type)`,
  `dormant_reason(plugin_id)` (`plugin_removed` | `plugin_disabled` | None), `printer_model_catalog()` (what `GET /printers/types` returns).
- **Dormancy:** a printer whose plugin is disabled/removed keeps its row + identity but `printer_manager.is_printer_ready` is False and
  `/fleet` flags `dormant`/`dormant_reason`. `printer_manager.set_printer_plugin(id, plugin_id)` records the mapping (create, import, startup).

## Status serialization (`AbstractPrinterClient.serialize_state`)

Each client returns its normalized dict (module-level `serialize_bambu` / `serialize_elegoo` / `serialize_snapmaker` in the plugin's
`client.py`, called by the method; the base default is `{id, printer_type, connected}`); `printer_manager.get_normalized_state` calls it and
overlays `connected`, `capabilities`, `awaiting_plate_clear`. Consumed by `fleet.py` and the WS `printer_state` broadcast.
Normalized keys: `state, current_print, progress, remaining_time, layer_num, total_layers,
temperatures, fan_model/aux/box, speed_factor, klippy_state, cover_url`. `loaded_filaments`/`awaiting_plate_clear`/`queue_on`/`enabled`
come from the DB row in `fleet.py`, not the serializer.

## Slicing pipeline (`SlicerService` → Laminus sidecar)

**As of the 2026-06-23 Orca-sidecar migration, Themis does not invoke OrcaSlicer locally for
production slicing.** `SlicerService.slice(SliceRequest)` resolves `machine_preset`/`process_preset`/
`filament_presets` names to UUIDs against the sidecar's cached profile catalog, then delegates the
whole slice — 3MF assembly, profile resolution, gcode generation — to a separate Laminus process over
HTTP (`SlicingProvider.slice` → Laminus adapter: `slice_start` → `poll_status` → `download`). The
pre-sidecar local pipeline (`providers/laminus/preset_resolver.py`, `providers/laminus/profile_index.py`, `project_config_builder.py`, and
most of `mesh_3mf_builder.py`) has no remaining callers — see `backend.md` § Services for what's dead.

**`SliceRequest.prepare_hook`** (opaque `Callable[[Path], None]`, built by `slicer_service.tool_mapping_hook(client, tool_index,
filament_map, provider)` in `queue_engine._run_slice_and_print` and the jobs route) still runs — but now against a
**job-scoped copy** of the source 3MF that `SlicerService` makes in the job's output directory, not the
original. `source_3mf` is the shared library file every job/printer referencing that upload resolves
to; running the hook on it directly would mutate it for every other consumer (this was a real
regression, fixed — see the git log around `slicer_service.py`). Recovery tier: on `SliceError`, the
sidecar itself retries geometry-only; `prepare_hook` isn't Themis's to re-invoke on that retry.
`filament_profile` (from the matched loaded slot) is passed as the filament preset; `filament_colours`
from the job ask.

**`mesh_3mf_builder`'s vendor-agnostic design carries over conceptually** even though its actual
3MF-building functions are now dead: `tool_index`/`filament_map` were never builder params — all
routing is delegated to the slicing provider's `apply_tool_mapping`, which still
holds under the sidecar architecture (it's applied to the job-scoped copy described above, same as
before).

**Filament→tool routing is a slicing-provider operation** (`SlicingProvider.apply_tool_mapping(source_3mf, *, tool_index, filament_map)`,
flag `TOOL_MAPPING`; base default raises `SlicingProviderError`; both args empty = no-op; both set = error):
- **Which printers:** only a client with `slice_tool_mapping = True` (Snapmaker) gets a hook — `tool_mapping_hook` returns None otherwise
  (Bambu realizes the mapping at print time via `ams_mapping`; Elegoo has none), so their 3MFs are never rewritten.
- **Laminus adapter** (`providers/laminus/adapter.py`): `TOOL_MAPPING = True`; delegates to `services/snapmaker/remap.remap_3mf(prepared,
  *, tool_index, filament_map)` (the code stays in Themis for now; it moves to Laminus's multi-material capability, BIZ-248). It rewrites the prepared 3MF in-place:
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
- The Snapmaker plugin must not import `services/snapmaker/` (`tests/test_tool_mapping_boundary.py`).

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

### Elegoo Centauri (`plugins/elegoo_centauri/client.py`) — SDCP
WebSocket `ws://<ip>:3030/websocket`; numeric `Cmd` IDs. Upload = single multipart **POST**
`http://<ip>:3030/uploadFile/upload` (`TotalSize/Uuid/Offset:0/Check:1/S-File-MD5` + file). `start_print`
(Cmd 128) needs the **`/local/<file>` path + params** `{StartLayer:0, Calibration_switch, PrintPlatformType,
Tlp_Switch}` — a bare filename is acked but won't start. **Ack quirk**: print-control results nest as
`Data.Data.Ack`; `_parse_response_msg` falls back to it (top-level `Result`/`Ack` for others). `stop`
is acked but **deferred during bed-flatness calibration** (lands after). Per-printer config:
`bed_type, bed_leveling, timelapse`. Full notes: `docs/elegoo-centauri-client.md`.

### Bambu (`plugins/bambu/client.py`) — MQTT + FTPS
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

### Snapmaker U1 Extended (`plugins/snapmaker/client.py`) — Moonraker/Klipper
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
applies the routing via the slicing provider's `apply_tool_mapping` → `snapmaker/remap.remap_3mf` (see Slicing pipeline above).
**Multi-material model→tool mapping** (Project 2b — delivered): `job_printer_configs.filament_map`
maps each declared model filament to a physical tool; routing applied via `apply_tool_mapping` →
`snapmaker/remap.remap_3mf` (rewrites `paint_color` bitstreams + object `extruder` metadata; see Slicing pipeline above).
**Validation status**: built + unit-tested; live Moonraker connectivity confirmed
(`scripts/snapmaker_smoke_test.py`, reads `SNAPMAKER_IP`); test print pending.
