# Data Model Reference

SQLite (WAL) via async SQLAlchemy 2.0 in `backend/app/models.py`. **No migration tool**: `init_db()`
runs `Base.metadata.create_all` (new tables) + idempotent `_migrate()` in `database.py` (added columns
on existing tables, via `PRAGMA table_info` guard + `ALTER TABLE … ADD COLUMN`). Dev DB at
`<data_dir>/themis.db`. To add a column to an existing table you MUST also add a `_migrate` guard or
existing DBs won't get it. JSON columns store Python lists/dicts.

## Tables (8)

```
printers            ← jobs.assigned_printer_id, job_printer_configs.printer_id, gcode_files.printer_id
uploaded_files      ← jobs.uploaded_file_id
orders              ← jobs.order_id (nullable; one order per job)
jobs                ← job_printer_configs.job_id, gcode_files.job_id
job_printer_configs
gcode_files
queue_config        (singleton-ish: check_interval_minutes)
spoolman_config     (enabled, url, api_key)
```

### printers
`id, name, printer_type` (factory key: `bambu`|`elegoo_centauri`), `connection_config: JSON`,
`awaiting_plate_clear: bool`, `orca_printer_profiles: JSON[str]`, `current_orca_printer_profile: str?`,
`enabled: bool`, `queue_on: bool`, `loaded_filaments: JSON`.
- `connection_config`: vendor creds **+ per-printer print options** (these are `connection_fields()`
  keys passed to the client ctor). Elegoo: `ip_address,bed_type,bed_leveling,timelapse`. Bambu:
  `ip_address,serial_number,access_code,use_ams,bed_leveling,flow_cali,timelapse`.
- `loaded_filaments`: list of `{slot:int, filament_id:str|null, name, type, color:"#RRGGBB",
  filament_profile?:str|null, spoolman_spool_id?:str|null, ams_tray_id?, ams_unit?}`.
  - `filament_id` = Bambu AMS material code (e.g. `"GFL99"`) or `null`; **not** a Spoolman id.
  - `filament_profile` = OrcaSlicer filament preset used when slicing with this slot.
  - `spoolman_spool_id` = optional mapped Spoolman spool id (written by EditForm/FilamentPicker).
  For AMS printers the list is **auto-synced** from the live AMS via `printer_manager.on_ams_change`
  (merge: per-slot `filament_profile`+`spoolman_spool_id` preserved; orphaned slots dropped); for
  others the user sets it via Fleet / EditForm. This is what the queue engine matches a job's ask against.

### uploaded_files
`id, original_filename, stored_path, plates: JSON, uploaded_at`.
- `plates`: `[{plate_number, estimated_time(min), filament_g, thumbnail_path}]` (parsed at upload).

### orders
`id, order_type` (`customer`|`internal`), `customer, title, due_date?, notes?`, `on_hold: bool`,
`parts: JSON, created_at, updated_at`.
- `parts`: BoM checklist `[{id, name, qty, material, est_minutes, filament_id?, filament_color?}]`. No
  per-part fulfillment tracking. **Derived (not stored)**: `status` (hold if on_hold; else queued/
  in_progress/complete from linked jobs), `progress` (completed/active jobs, 0..1), `job_count`.

### jobs
`id, uploaded_file_id FK, plate_number, order_id FK?, assigned_printer_id FK?, queue_position: float?`
(float → reorder without renumber), `status, block_reason: text?, created_at, updated_at`.
- status enum: `queued|slicing|uploading|printing|paused|complete|blocked|failed|cancelled`.

### job_printer_configs  (one row per (job, eligible printer))
`id, job_id FK, printer_id FK, print_profile` (orca process preset), `filament_profile?` (legacy /
manual-type fallback; the *authoritative* orca filament preset for slicing now lives on the printer's
loaded-filament slot), `filament_id?` (Spoolman), `filament_type, filament_color` (the job's filament
**ask** → matched against `printer.loaded_filaments`), `slice_failed: bool, slice_error: text?`.
- `filament_type`+`filament_color` = the eligibility "ask". `slice_failed` blocks the job on that
  printer until cleared (by `unblock` or `updateJobConfigs`).

### gcode_files
`id, job_id FK, printer_id FK, path`.

### queue_config / spoolman_config
`queue_config{check_interval_minutes:int=5}`. `spoolman_config{enabled, url?, api_key?}`.

## Frontend ↔ backend shape contracts

- Job API dicts emit `order_id` (not `project_id` — that was renamed; `projects` table removed).
- `ApiOrder.status: StatusKey`, `progress: number` (0..1, ×100 for the bar).
- `LoadedFilament` (frontend `api/printers.ts`) mirrors the slot dict; `filament_id` is Bambu AMS code or null (not Spoolman); `filament_profile?` and `spoolman_spool_id?` are optional.
- `loaded_filaments` reaches the Fleet UI via `fleet.py` merging the DB row over the live state.
