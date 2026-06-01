# Backend Reference

FastAPI app at `backend/app/main.py`. Routes under `app/api/routes/`, business logic under
`app/services/`, ORM in `app/models.py`, engine/session/migrate in `app/database.py`, env config in
`app/config.py`. All routers + the `/ws` endpoint are registered in `main.py`; the `lifespan` wires
the three subsystems and connects enabled printers.

## Routes (`app/api/routes/`)

Each module = one `APIRouter(prefix="/api/v1/<x>")`. Endpoints below are the public contract.

| Module | Prefix | Key endpoints (method path → purpose) |
|---|---|---|
| `files.py` | `/api/v1/files` | `POST /upload` (store 3MF/STL, parse plates), `GET /{id}/plates`, `GET /{id}/thumbnails/{name}` |
| `jobs.py` | `/api/v1/jobs` | `POST ""` create, `GET ""`/`GET /{id}`, `GET /{id}/details` (full: file/plate/per-printer configs/assigned), `POST /check-overrides`, `PATCH /{id}/configs` (replace configs + re-queue), `POST /{id}/unblock` (clear slice_failed + re-queue top), `POST /{id}/cancel` (→ stops printer if running), `GET /{id}/slice-failures` |
| `orders.py` | `/api/v1/orders` | CRUD; list/get carry derived `status`+`progress`+`job_count`; `GET /{id}` adds linked `jobs`; `DELETE` nulls `order_id` on jobs |
| `printers.py` | `/api/v1/printers` | `GET /types` (vendor descriptors for add-form), `POST ""`/`GET`/`PATCH`/`DELETE /{id}`, `POST /test-connection`, `GET /{id}/profiles` (compatible orca process+filament presets), `GET /orca-machine-catalog`, `POST /rescan-profiles`, `POST /{id}/plate-cleared` (ready-for-work), control: `pause`/`resume`/`stop`(→reconciles job)/`light`/`jog-z`/`fan`/`bed-temp`/`reconnect`, camera: `GET /{id}/camera`(MJPEG), `GET /{id}/snapshot` |
| `queue.py` | `/api/v1/queue` | `GET ""` (active jobs ordered), `PATCH /reorder` |
| `fleet.py` | `/api/v1/fleet` | `GET ""` — per-printer merge of DB row (`enabled,queue_on,awaiting_plate_clear,loaded_filaments`) + live `printer_manager.get_normalized_state` |
| `settings.py` | `/api/v1/settings` | `GET/PUT /queue` (check interval), `GET/PUT /spoolman`, `POST /spoolman/test` |
| `spoolman.py` | `/api/v1/spoolman` | `GET /filaments`, `GET /spools` (proxy to Spoolman) |

Pattern for a route: define Pydantic `*Create`/`*Patch` models, a `_to_dict(row)` serializer, a
`_get_or_404`, use `session: AsyncSession = Depends(get_session)`. `HTTPException(404, "msg")` uses
**positional** detail here (match existing style). Register the router in `main.py`.

## Services (`app/services/`)

| File | Responsibility / key symbols |
|---|---|
| `abstract_printer_client.py` | `AbstractPrinterClient` ABC, `ConnectionField`, `PrinterCapabilities`, `StartPrintOptions`, `PrinterFile`. Defines what a vendor client must implement. See `printers.md`. |
| `printer_client_factory.py` | `REGISTRY` (printer_type→class path), `get_printer_types_for_ui()`, `create_client(printer)` / `create_client_from_config(type,cfg)` (pass only `connection_fields()` keys to the ctor). Add a vendor here. |
| `printer_manager.py` | Singleton `printer_manager`. `_clients: {id: client}`. `_STATUS_SERIALIZERS` (per type → normalized fleet dict). `is_printer_ready` = `client.is_idle AND id not in _awaiting_plate_clear`. `set/is_awaiting_plate_clear`. `connect_printer` wires `_on_state_change`/`_on_print_complete`/`_on_ams_change`. `on_print_complete` sets awaiting + broadcasts `plate_clear_required`. `on_ams_change` syncs AMS trays → DB `loaded_filaments`. `get_normalized_state(id)` = serializer + capabilities + awaiting. |
| `queue_engine.py` | Singleton `queue_engine`. `wake()` sets the event. `_process_queue` claims; `_try_claim_for_printer` (head-of-line: filament/slice mismatch ⇒ `_block_job`, no skip-ahead). `_run_slice_and_print` (thread-pooled slice → upload → start; sets `awaiting_plate_clear=True` on `status=printing`; builds `ams_mapping` from matched tray). `_matching_loaded_filament(config, loaded)` (type+color match; no requirement ⇒ first slot). `_handle_slice_failure` (mark config.slice_failed, block or fail-if-exhausted). |
| `slicer_service.py` | `SlicerService.slice(SliceRequest) -> path`. Resolves presets → `build_sliceable_3mf` (embedded config) → runs `[orca, --slice N, --outputdir, *export_args, input]` → returns artifact (raw `.gcode` or `--export-3mf` target). Recovery tier: retry geometry-only on `SliceError`. Exe/dir from `config.get_orca_executable/_config_dir`. |
| `preset_resolver.py` | `PresetResolver`: resolve OrcaSlicer inheritance-diff presets → flat config; indexes machine/process/filament JSONs under the orca config dir. |
| `profile_index.py` | `ProfileIndex`: cached catalog of real machine presets `[{name,vendor,printer_model,nozzle,source}]` + `(model,nozzle)→compatible process/filament` map. `machine_catalog()`, `compatible_profiles(machine_preset)`. Rebuilds on user-preset mtime change. |
| `project_config_builder.py` | `build_project_config(machine, process, filaments, colours)` → the merged config embedded in the sliceable 3MF. |
| `mesh_3mf_builder.py` | `build_sliceable_3mf(src, config, out, geometry_only)`, `stl_to_3mf`, `source_has_project_settings`. |
| `override_inspector.py` | `inspect_overrides(...)` — diffs settings baked into an uploaded 3MF vs chosen presets (New Job "won't carry over" warning). |
| `three_mf_parser.py` | Parse plates/metadata from an uploaded 3MF. |
| `camera_proxy.py` | `grab_jpeg_frame`, `stream_mjpeg`, `stream_rtsp_ffmpeg` (RTSP→MJPEG via ffmpeg). |
| `spoolman_service.py` | Spoolman HTTP client (filaments/spools). |

## Key flows (where to change behavior)

- **Claim eligibility** → `printer_manager.is_printer_ready` + `queue_engine._try_claim_for_printer`. Gate add/changes here.
- **Filament gating / AMS mapping** → `_matching_loaded_filament` + the slot dict shape (`type,color,filament_profile,ams_tray_id`). DB `printer.loaded_filaments` is the source (AMS auto-synced via `on_ams_change`).
- **Slice → print** → `_run_slice_and_print`. `client.orca_export_args` decides artifact format; `start_print(opts)` with `ams_mapping` for AMS vendors.
- **Plate-clear / ready-for-work** → set on `status=printing` in `_run_slice_and_print`; cleared by `/plate-cleared`. `is_printer_ready` reads it.
- **Cancel ↔ stop** → `jobs.cancel_job` stops the assigned printer (`asyncio.to_thread(client.stop_print)`) when status in `{printing,paused,uploading}`; `printers.stop_printer` reconciles the running job → `cancelled`.

## Database & config

- `database.py`: async engine (SQLite WAL), `SessionLocal`, `get_session` dep, `init_db()` =
  `create_all` + `_migrate()`. **No migration tool** — add columns to existing tables via an
  idempotent `ALTER TABLE … ADD COLUMN` guard in `_migrate()`; new tables are created by `create_all`.
- `config.py`: `get_data_dir`, `get_orca_config_dir`, `get_orca_executable`, `get_ffmpeg_executable`.
  Defaults are **platform-aware** (Windows local dev resolves `%APPDATA%\OrcaSlicer` and the Program
  Files `orca-slicer.exe`); env vars `THEMIS_DATA_DIR`/`ORCA_CONFIG_DIR`/`ORCA_EXECUTABLE`/
  `FFMPEG_EXECUTABLE` override.

## Tests

`backend/tests/` (pytest-asyncio). `conftest.py` `client` fixture = httpx + in-memory SQLite +
`get_session` override. Unit tests for services in `tests/services/`. To seed DB state inside an API
test, reuse the override: `agen = app.dependency_overrides[get_session](); session = await agen.__anext__()`.
