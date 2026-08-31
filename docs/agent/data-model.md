# Data Model Reference

SQLite (WAL) via async SQLAlchemy 2.0 in `backend/app/models.py`. Migrations run automatically at
startup via `backend/app/migrations/runner.py` (Flyway-style versioned files in
`backend/app/migrations/v00N_name.py`). Dev DB at `<data_dir>/themis.db`. To add a column to an
existing table, create a new migration file. JSON columns store Python lists/dicts.

## Tables (22)

```
printers            ← jobs.assigned_printer_id, job_printer_configs.printer_id, gcode_files.printer_id,
                       printer_maintenance_state.printer_id
api_keys            (standalone — no FKs)
bootstrap_sentinel  (standalone — no FKs; see its own section below)
uploaded_files      ← jobs.uploaded_file_id, file_tags.file_id, project_items.file_id,
                       projects.result_file_id
tags                ← file_tags.tag_id
file_tags           (junction: file_id + tag_id, both CASCADE DELETE)
orders              ← jobs.order_id (nullable), projects.order_id (nullable)
jobs                ← job_printer_configs.job_id, gcode_files.job_id, job_item_failures.job_id
job_printer_configs
gcode_files
queue_config        (singleton id=1: check_interval_minutes, operator_name, snapshot_interval_seconds,
                       estimates_enabled)
spoolman_config     (enabled, url, api_key)
webhook_config      (singleton id=1: url?, secret?, events: JSON[str])
notification_config (singleton id=1: ntfy/discord/email — see its own section below)
projects            ← project_items.project_id, project_links.project_id, project_parts.project_id,
                       jobs.project_id
project_items       ← job_item_failures.project_item_id
project_links       (junction-free child: project_id CASCADE, url, label?, sort_order, created_at)
project_parts       (junction-free child: project_id CASCADE, name, quantity, allocated, sort_order,
                       created_at — non-3D-printed hardware for the assembly, e.g. magnets/screws)
job_item_failures
maintenance_items       ← maintenance_triggers.maintenance_item_id, printer_maintenance_state.maintenance_item_id
maintenance_triggers    (child: maintenance_item_id CASCADE, trigger_type, amount, unit)
printer_maintenance_state (child: printer_id CASCADE, maintenance_item_id CASCADE, UNIQUE(printer_id, maintenance_item_id))
```

### printers
`id, name, printer_type` (factory key: `bambu`|`elegoo_centauri`|`snapmaker_extended`), `connection_config: JSON`,
`awaiting_plate_clear: bool`, `orca_printer_profiles: JSON[str]`, `current_orca_printer_profile: str?`,
`enabled: bool`, `queue_on: bool`, `loaded_filaments: JSON`, `build_plate_type: str?` (OrcaSlicer
`curr_bed_type` override, merged into `SliceRequest.extra_config`), `no_snapshots_while_idle: bool`
(Fleet camera-polling toggle), `bed_x_mm: float=256.0, bed_y_mm: float=256.0` (bed footprint, shown in
the printer editor and used wherever bed size matters for the UI), `lifetime_job_count: int` (accrued in
`queue_engine.handle_print_complete`, +1 per successful completion), `lifetime_print_seconds: int`
(accrued from `job.actual_seconds` in the same handler) — both feed `job_count`/`job_time` maintenance
trigger math, never reset except by construction (per-item resets live on `printer_maintenance_state`).
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

**Actual values** (set at production slice time, before the `gcode_files` row is deleted):
`actual_filament_grams: float?, actual_seconds: int?, actual_filament_breakdown: JSON?,
deduction_skipped: bool?`. `deduction_skipped` is set `True` when the queue engine can't confidently
deduct consumed filament from Spoolman (e.g. no matched spool) — see `queue_engine.py` around where
`lifetime_print_seconds` is accrued.

**Estimate values** (set by an optional background test-slice, gated by `queue_config.estimates_enabled`):
`estimate_token: int=0, estimate_status: str?` (`pending|done|failed|null`), `estimate_seconds: int?,
estimate_filament_grams: float?, estimate_filament_breakdown: JSON?, estimate_preset_label: JSON?`.
`queue_engine.spawn_estimate(job_id)` → `run_estimate` → `_do_run_estimate` runs a geometry-only test
slice off the queue's normal path and writes the result back with a `WHERE estimate_status='pending' AND
estimate_token=:token` guard — `estimate_token` is bumped on every re-request (job edit, unblock) so a
slow/stale estimate task can never clobber a newer one's result; a mismatched token on write is a no-op,
not an error. `estimate_filament_grams` (falling back to the plate's parsed `filament_g` when no
estimate exists yet) is what `spool_check.check_spool_sufficiency` compares against a bound spool's
remaining weight for the low-stock warning (see `backend.md` § Services, `spool_check.py`).

### job_printer_configs  (one row per (job, eligible printer))
`id, job_id FK, printer_id FK, print_profile` (orca process preset), `filament_profile?` (legacy /
manual-type fallback; the *authoritative* orca filament preset for slicing now lives on the printer's
loaded-filament slot), `filament_id?` (Spoolman), `filament_type, filament_color` (the job's filament
**ask** → matched against `printer.loaded_filaments`), `tool_index?` (nullable int, 0-based physical
tool/slot; `None` = default/legacy — queue uses type+color ask instead),
`filament_map?` (JSON, nullable), `slice_failed: bool, slice_error: text?`.
- `filament_type`+`filament_color` = the eligibility "ask". Non-nullable, `server_default="any"` — the
  literal string `"any"` (never null/blank) means no constraint on that axis; matching logic checks for
  this keyword rather than a null/empty check. Same convention on `project_items.filament_type/color`
  below. `slice_failed` blocks the job on that printer until cleared (by `unblock` or `updateJobConfigs`).
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

### queue_config / spoolman_config / webhook_config / notification_config
`queue_config{check_interval_minutes:int=5, operator_name:str?, snapshot_interval_seconds:int=2,
estimates_enabled:bool=False}`. `estimates_enabled` gates the background test-slice estimate pipeline
(see `jobs` § Estimate values above); flipping it off does not clear already-computed estimates.
Managed via `GET/PUT /api/v1/settings/queue`.

`spoolman_config{enabled, url?, api_key?}`. Managed via `GET/PUT /api/v1/settings/spoolman`,
`POST /api/v1/settings/spoolman/test`.

`webhook_config` (singleton id=1): `{url:str?, secret:str?, events:JSON[str]}`. When `url` is set, the
queue engine fires a signed `POST` on `job.complete`, `job.failed`, and `job.blocked` events (filtered by `events`
list — **empty list means all**). Signature header: `X-Webhook-Signature: sha256=<hmac-sha256>`.
Managed via `GET/PUT /api/v1/settings/webhook`.

`notification_config` (singleton id=1) — three independent built-in channels, additive alongside
`webhook_config` (not a replacement): `ntfy_{enabled,server_url,topic,priority,events}`,
`discord_{enabled,webhook_url,events}`, `email_{enabled,host,port,username,password,from_addr,
to_addrs,events}`. Each channel's own `*_events: JSON[str]` list is evaluated independently —
**empty list means *none*, the opposite of `webhook_config.events`'s "empty means all"**; this is an
intentional per-channel opt-in, not a bug, but don't assume the two behave the same way. Dispatch:
`notification_service.dispatch(cfg, event, ...)` fans out to whichever channels are enabled and have
the firing event in their own list; fired via `asyncio.create_task` (never awaited directly) from
`queue_engine._fire_notifications`, alongside `_fire_webhooks`, on the same three job events as
`webhook_config`. Managed via `GET/PUT /api/v1/settings/notifications`,
`POST /api/v1/settings/notifications/test` (send-test with unsaved in-form values, not read from DB).

### projects
`id, name, customer:str="", order_type:str="internal"` (`"customer"`|`"internal"` — same vocabulary as
`orders.order_type`, but this is the project's own field, not a copy of the linked order's), `on_hold:
bool, due_date?, machine_uuid?, process_uuid?, notes?, result_file_id FK?, order_id FK?, source_app?,
source_user?, source_layout_id?, created_at, updated_at`.
- Full CRUD at `/api/v1/projects`. Created by Themis UI (Project Builder) or by Ordinus
  (`source_app="ordinus"`, `source_layout_id=<ordinus BOM id>`).
- `customer`/`order_type`/`on_hold`/`due_date` are the project's own customer-facing fields (set/edited
  directly via the Project Builder), independent of whether it's linked to an `orders` row.
- `order_id`: set by `generate_project` — the internal `orders` row that groups all generated jobs for
  fulfillment tracking. `NULL` until the project is first generated. Not the same thing as the
  project's own `order_type` field above.
- `machine_uuid`/`process_uuid`: kept for backward compat with the legacy pre-generate-flow; not shown
  in the current UI.
- `result_file_id`: legacy single-result pointer from pre-generate-flow projects. Cleared when
  `generate` is called.

### project_items
`id, project_id FK (CASCADE), file_id FK (RESTRICT), quantity, quantity_completed, quantity_failed,
filament_type:str="any", filament_color:str="any", filament_id:int?, color_hex:str="#FFFFFF"
(legacy), sort_order`.
- One row per STL file in the project. `quantity` = how many copies to pack.
- `quantity_completed`/`quantity_failed` are updated as jobs for this project complete.
- `filament_type`/`filament_color`: the item's own filament requirement spec, same `"any"`-keyword
  convention as `job_printer_configs` above. `filament_id`: Spoolman filament id. `color_hex` is a
  legacy OrcaSlicer-era field kept for backward compat with pre-v005 rows; not the current color source.

### project_links
`id, project_id FK (CASCADE), url, label?, sort_order, created_at`.
- User-defined URLs attached to a project (e.g. a spec doc or reference link). Full CRUD at
  `/api/v1/projects/{project_id}/links`. Rendered read-only on `ProjectDetailScreen`.

### project_parts
`id, project_id FK (CASCADE), name, quantity, allocated: bool, sort_order, created_at`.
- Non-3D-printed parts needed to complete the project's assembly (e.g. "3mm magnet" ×5, "M3 screw" ×2)
  — lets a project encapsulate a full BOM, not just the printed pieces. `allocated` is a manual
  yes/no flag the user sets to record that stock has been set aside for this project (no automatic
  inventory tracking). Full CRUD at `/api/v1/projects/{project_id}/parts`
  (`GET`/`POST` list+create, `PUT`/`DELETE /{part_id}`). Editable on `ProjectBuilderScreen`; the
  `allocated` checkbox is also toggleable directly from `ProjectDetailScreen` (optimistic update via
  `PUT .../parts/{id}`).

### job_item_failures
`id, job_id FK (CASCADE), project_item_id FK (CASCADE), quantity_failed, quantity_on_plate`.
- Written when a job fails to record how many of each project item were on that plate.

### maintenance_items
`id, name, scope` (`"general"` | `"model"`), `machine_vendor: str?, machine_model: str?` (set only when
`scope="model"`, matched against `GET /printers/orca-machine-catalog`'s `vendor`/`printer_model` —
**not** `printers.printer_type`, which is too coarse), `enabled: bool, notes: str?, created_at, updated_at`.
- Full CRUD at `/api/v1/maintenance/items`. A `"general"` item applies to every printer; a `"model"` item
  applies only to printers whose resolved `(vendor, printer_model)` matches exactly.

### maintenance_triggers
`id, maintenance_item_id FK (CASCADE), trigger_type` (`"calendar"` | `"job_time"` | `"job_count"`),
`amount: float, unit: str?` (`"hours"|"days"|"weeks"|"months"`, calendar-only — `null` otherwise).
- One item has 0+ triggers; due-ness is `any()` across them (`maintenance_service._trigger_due`) — the
  item is due the moment the *first* trigger crosses its threshold, not all of them. Replaced wholesale
  (delete-all/recreate-all) via `PUT /api/v1/maintenance/items/{id}/triggers`, not diffed in place.

### printer_maintenance_state
`id, printer_id FK (CASCADE), maintenance_item_id FK (CASCADE)` with `UNIQUE(printer_id,
maintenance_item_id)`, `last_done_at: str, baseline_job_count: int, baseline_print_seconds: int`.
- Lazily created on first `compute_due_status` evaluation for a (printer, item) pair, or explicitly on
  `POST /api/v1/maintenance/printers/{printer_id}/items/{item_id}/complete` ("mark done"). Baselines
  default to `0`/`0` for job_count/job_time triggers (so a newly-added item reflects the printer's full
  lifetime wear, not a clock that silently starts at "whenever someone first checked"); `last_done_at`
  defaults to *now* (so a newly-added calendar trigger doesn't appear instantly overdue) — this asymmetry
  is intentional, see the comment at `maintenance_service.py::_get_or_init_state`.
- `printers.lifetime_job_count`/`lifetime_print_seconds` (see the `printers` entry above) are the only
  counters these baselines are diffed against; `mark_done` resets both baselines to the printer's current
  lifetime counters.

### api_keys
`id, name, key_prefix` (unique, indexed — first `PREFIX_LEN`=12 chars of the raw key, e.g. `thm_ab12cd34`,
unhashed, used for fast lookup before the hash compare), `key_hash` (sha256 hex of the full raw key),
`scopes: JSON[str]`, `enabled: bool, created_at, last_used_at?, revoked_at?, expires_at?`. A key past
`expires_at` is treated as invalid by `require_scope`'s resolution the same as `enabled=False`, without
needing an explicit revoke.
- The raw key itself is never stored — only `key_prefix` (for lookup) + `key_hash` (for verification).
  Shown to the user exactly once, in the `POST /api/v1/api-keys` response.
- `scopes` is a subset of the fixed `SCOPES` registry in `app/auth.py` (see `backend.md`'s Auth section).
- No FKs — standalone credential table, not linked to any other resource.

### bootstrap_sentinel
`id, created_at`. Not a config table — a concurrency guard. `POST /api-keys` bootstraps (grants full
`SCOPES` regardless of requested scopes) whenever `api_keys` is empty; two racing requests (e.g. two
browser tabs on first load) could otherwise both see it empty and both bootstrap. The handler inserts
`BootstrapSentinel(id=1, ...)` inside the same flush — the fixed PK makes the second concurrent insert
raise `IntegrityError`, so only one request wins the bootstrap path; the loser falls through to the
normal (non-bootstrap, caller-specified-scopes) create-key flow. Never has more than one row.

## Migrations

See `backend/app/migrations/` for versioned migration files. `runner.py` applies pending migrations
at startup — **not** `Base.metadata.create_all()`, which is never called in production (only in test
fixtures). To add a column or table:

1. Create `backend/app/migrations/v00N_your_name.py`:
   ```python
   version = N
   name = "your_name"
   async def up(conn):
       # new column: idempotent guard first
       cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(foo)"))).fetchall()}
       if "bar" not in cols:
           await conn.execute(text("ALTER TABLE foo ADD COLUMN bar TEXT"))
       # new table: CREATE TABLE IF NOT EXISTS ...
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
- `low_stock_warning: {spool_id, spool_label, remaining_g, needed_g, message} | null` — on each job in
  `GET /api/v1/queue` and on each `printer_configs[]` entry in `GET /api/v1/jobs/{id}/details`. Built by
  `spool_check.check_spool_sufficiency`; `null` means either "sufficient" or "nothing to check yet"
  (no bound spool, Spoolman unreachable) — the frontend treats both the same, never as an error state.

None of these shapes are shared via codegen — every TS type mirroring a backend response is hand-kept
in sync. See `backend-review.md`/`frontend-review.md` §1 before changing a field on either side.
