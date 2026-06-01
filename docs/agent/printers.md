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

`REGISTRY = {"bambu": "...BambuMQTTClient", "elegoo_centauri": "...ElegooCentauriClient"}` + a
display-name map. `create_client(printer)`/`create_client_from_config(type, cfg)` pass only
`connection_fields()` names from `connection_config` into the ctor (+ callbacks if the ctor accepts them).
Add a vendor → add both entries; everything else auto-wires.

## Status serialization (`printer_manager._STATUS_SERIALIZERS`)

`type → fn(state, id) -> normalized dict` consumed by `fleet.py` and the WS `printer_state` broadcast.
Normalized keys: `state, current_print, progress, remaining_time, layer_num, total_layers,
temperatures, fan_model/aux/box, speed_factor, klippy_state, cover_url`. A new vendor needs a serializer
entry. `loaded_filaments`/`awaiting_plate_clear`/`queue_on`/`enabled` come from the DB row in `fleet.py`,
not the serializer.

## Slicing pipeline (`SlicerService` + `mesh_3mf_builder` + `project_config_builder`)

OrcaSlicer's CLI cannot set the active printer via `--load-settings`, so Themis **generates an
embedded-config 3MF** and slices that. Flow: `_build_config` (resolve machine+process+filament presets
through inheritance via `PresetResolver`, merge via `build_project_config`) → `build_sliceable_3mf`
(embed config, preserve model_settings/overrides) → run `[orca, --slice N, --outputdir, *export_args,
input]` → return artifact. Recovery tier: on `SliceError`, retry `geometry_only=True`. `filament_profile`
(from the matched loaded slot) is passed as the filament preset; `filament_colours` from the job ask.
See the `slicer-cli-architecture` memory for the multicolor model.

## Filament gating & AMS

Queue claim matches the job's **ask** (`config.filament_type`+`filament_color`) against the printer's
`loaded_filaments` (`_matching_loaded_filament`, type+color, case/`#`-insensitive; empty ask ⇒ first
slot). The matched slot supplies `filament_profile` (orca preset for slicing) and, for AMS,
`ams_tray_id` → `StartPrintOptions.ams_mapping=[id]`. Mismatch ⇒ job **blocked** (transient).

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
