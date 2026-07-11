# Data Model Reference

SQLite (WAL) via async SQLAlchemy 2.0 in `backend/app/models.py`. Migrations run automatically at
startup via `backend/app/migrations/runner.py` (Flyway-style versioned files in
`backend/app/migrations/v00N_name.py`). Dev DB at `<data_dir>/themis.db`. To add a column to an
existing table, create a new migration file. JSON columns store Python lists/dicts.

## Tables (14)

```
printers            ← jobs.assigned_printer_id, job_printer_configs.printer_id, gcode_files.printer_id
uploaded_files      ← jobs.uploaded_file_id, file_tags.file_id, project_items.file_id,
                       projects.result_file_id
tags                ← file_tags.tag_id
file_tags           (junction: file_id + tag_id, both CASCADE DELETE)
orders              ← jobs.order_id (nullable), projects.order_id (nullable)
jobs                ← job_printer_configs.job_id, gcode_files.job_id, job_item_failures.job_id
job_printer_configs
gcode_files
queue_config        (singleton-ish: check_interval_minutes, operator_name)
spoolman_config     (enabled, url, api_key)
webhook_config      (singleton id=1: url?, secret?, events: JSON[str])
projects            ← project_items.project_id, jobs.project_id
project_items       ← job_item_failures.project_item_id
job_item_failures
```

### printers
`id, name, printer_type` (factory key: `bambu`|`elegoo_centauri`|`snapmaker_extended`), `connection_config: JSON`,
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
Library index fields (filesystem is source of truth; these cache it):
`relative_path, folder, size_bytes, content_hash, mtime: float, missing: bool`.
- `plates`: `[{plate_number, estimated_time(min), filament_g, thumbnail_path}]` (parsed at upload).
- `folder` defaults to `"/"`. `missing` is set by `library_scanner` when the file can't be found.

### tags
`id, name (unique), color: str ("#RRGGBB" default "#64748b"), category: str, created_at`.

### file_tags
`file_id FK → uploaded_files (CASCADE), tag_id FK → tags (CASCADE)`. Composite PK.

### orders
`id, order_type` (`customer`|`internal`), `customer, title, due_date?, notes?`, `on_hold: bool`,
`parts: JSON, created_at, updated_at`.
- `parts`: BoM checklist `[{id, name, qty, material, est_minutes, filament_id?, filament_color?}]`. No
  per-part fulfillment tracking. **Derived (not stored)**: `status` (hold if on_hold; else queued/
  in_progress/complete from linked jobs), `progress` (completed/active jobs, 0..1), `job_count`.
- Internal orders (`order_type="internal"`) are auto-created by `generate_project` and linked to a
  Project via `projects.order_id`. All jobs generated for that project also set `job.order_id`.

### jobs
`id, uploaded_file_id FK, plate_number, order_id FK?, assigned_printer_id FK?, queue_position: float?`
(float → reorder without renumber), `status, project_id FK?, block_reason: text?, overrides: JSON?,
project_item_quantities: text?, created_at, updated_at, completed_at?, outcome?`.
- `overrides`: optional dict of OrcaSlicer setting overrides applied at slice time; validated via `override_inspector`.
- `project_id`: set when a job is created by `generate_project`. SET NULL on project delete.
- `project_item_quantities`: JSON dict mapping `project_item_id → quantity_on_this_plate`.
- status enum: `queued|slicing|uploading|printing|paused|complete|blocked|failed|cancelled`.

### job_printer_configs  (one row per (job, eligible printer))
`id, job_id FK, printer_id FK, print_profile` (orca process preset), `filament_profile?` (legacy /
manual-type fallback; the *authoritative* orca filament preset for slicing now lives on the printer's
loaded-filament slot), `filament_id?` (Spoolman), `filament_type, filament_color` (the job's filament
**ask** → matched against `printer.loaded_filaments`), `tool_index?` (nullable int, 0-based physical
tool/slot; `None` = default/legacy — queue uses type+color ask instead),
`filament_map?` (JSON, nullable), `slice_failed: bool, slice_error: text?`.
- `filament_type`+`filament_color` = the eligibility "ask". `slice_failed` blocks the job on that
  printer until cleared (by `unblock` or `updateJobConfigs`).
- `tool_index`: when set, `_slot_for_config` resolves `loaded_filaments[tool_index]` directly (bypasses
  type/color match); `_filament_mismatch` checks that slot is loaded.
- `filament_map`: multi-material model→tool mapping. Shape: `[{model_filament: int (1-based),
  tool_index: int (0-based)}, …]`; `null` = single-material (no remap). When set, queue passes
  loaded slots ordered by tool as N `filament_presets` and forwards the map into `SliceRequest`;
  `_mapped_tools_loaded` gates eligibility on every mapped tool having a loaded filament.

### gcode_files
`id, job_id FK, printer_id FK, path, filament_grams: float?, estimated_seconds: int?`.
- `filament_grams` / `estimated_seconds`: parsed from the gcode header after slice completes (OrcaSlicer
  emits `; filament used [g] = X` and `; estimated printing time = Xh Xm Xs`).
  Exposed on `GET /api/v1/jobs/{id}/details` as `filament_grams` / `estimated_seconds`.
  Aggregated per-project in the project dict as `filament_grams` / `estimated_seconds`.
  Row deleted when print completes or job is cancelled.

### queue_config / spoolman_config / webhook_config
`queue_config{check_interval_minutes:int=5, operator_name:str?, snapshot_interval_seconds:int=2}`.
`spoolman_config{enabled, url?, api_key?}`.
`webhook_config` (singleton id=1): `{url:str?, secret:str?, events:JSON[str]}`. When `url` is set, the
queue engine fires a signed `POST` on `job.complete`, `job.failed`, and `job.blocked` events (filtered by `events`
list — empty list means all). Signature header: `X-Webhook-Signature: sha256=<hmac-sha256>`.
Managed via `GET/PUT /api/v1/settings/webhook`.

### projects
`id, name, machine_uuid?, process_uuid?, notes?, result_file_id FK?, order_id FK?, source_app?,
source_user?, source_layout_id?, created_at, updated_at`.
- Full CRUD at `/api/v1/projects`. Created by Themis UI (Project Builder) or by Ordinus
  (`source_app="ordinus"`, `source_layout_id=<ordinus BOM id>`).
- `order_id`: set by `generate_project` — the internal Order that groups all generated jobs.
  `NULL` until the project is first generated.
- `result_file_id`: legacy single-result pointer from pre-generate-flow projects. Cleared when
  `generate` is called.

### project_items
`id, project_id FK (CASCADE), file_id FK (RESTRICT), quantity, quantity_completed, quantity_failed,
filament_profile_uuid, color_hex, sort_order`.
- One row per STL file in the project. `quantity` = how many copies to pack.
- `quantity_completed`/`quantity_failed` are updated as jobs for this project complete.

### job_item_failures
`id, job_id FK (CASCADE), project_item_id FK (CASCADE), quantity_failed, quantity_on_plate`.
- Written when a job fails to record how many of each project item were on that plate.

## Migrations

See `backend/app/migrations/` for versioned migration files. `runner.py` applies pending migrations
at startup. To add a column:

1. Create `backend/app/migrations/v00N_your_name.py`:
   ```python
   version = N
   name = "your_name"
   async def up(conn): await conn.execute(text("ALTER TABLE foo ADD COLUMN bar TEXT"))
   async def down(conn): ...
   ```
2. Register it in `runner.py`: `from . import ..., v00N_your_name`; add to `_MIGRATIONS`.

CLI: `cd backend && python -m app.migrations.migrate up|down`.

## Frontend ↔ backend shape contracts

- Job API dicts emit both `order_id` (the linked order, if any) and `project_id` (the linked project,
  if any). These are independent nullable FKs on the jobs table.
- `ApiOrder.status: StatusKey`, `progress: number` (0..1, ×100 for the bar).
- `LoadedFilament` (frontend `api/printers.ts`) mirrors the slot dict; `filament_id` is Bambu AMS code or null (not Spoolman); `filament_profile?` and `spoolman_spool_id?` are optional.
- `loaded_filaments` reaches the Fleet UI via `fleet.py` merging the DB row over the live state.
