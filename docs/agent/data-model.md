# Data Model Reference

SQLite (WAL) via async SQLAlchemy 2.0 in `backend/app/models.py`. Migrations run automatically at
startup via `backend/app/migrations/runner.py` (Flyway-style versioned files in
`backend/app/migrations/v00N_name.py`). Dev DB at `<data_dir>/themis.db`. To add a column to an
existing table, create a new migration file. JSON columns store Python lists/dicts.

## Tables (25)

```
printers            ← jobs.assigned_printer_id, job_printer_configs.printer_id, gcode_files.printer_id,
                       printer_maintenance_state.printer_id
customers           ← projects.customer_id (SET NULL), api_keys.customer_id (CASCADE)
admin_account       (singleton id=1 — see its own section below)
api_keys            (customer_id FK? — set only on customer login sessions)
bootstrap_sentinel  (retired — no model, created by v015 only; never read or written)
uploaded_files      ← jobs.uploaded_file_id, file_tags.file_id, project_items.file_id,
                       projects.result_file_id
tags                ← file_tags.tag_id
file_tags           (junction: file_id + tag_id, both CASCADE DELETE)
orders              ← jobs.order_id (nullable), projects.order_id (nullable)
jobs                ← job_printer_configs.job_id, gcode_files.job_id, job_item_failures.job_id
job_printer_configs  ← (model_target_id, plain int → job_model_targets.id, no FK)
job_model_targets   (job_id CASCADE; v031)
gcode_files
sliced_versions     (file_id CASCADE → uploaded_files, source_file_id SET NULL → uploaded_files; v033)
queue_config        (singleton id=1: check_interval_minutes, operator_name, snapshot_interval_seconds,
                       estimates_enabled, slice_cache_use_latest_settings)
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
`quiet_start` / `quiet_end: str?` (v028) — server-local `HH:MM` window (wraps midnight; both or neither, validated in `PrinterUpdate`) in which a *ready* printer starts no new jobs (neither claims nor resumes pre-sliced gcode); running prints are never interrupted and offline slice-ahead still happens. The end of a window is noticed at the next periodic queue check (no dedicated wake). The UI times are server-local (UTC in a default Docker container). Logic in `services/scheduling.py::in_quiet_hours`.

### uploaded_files
`id, original_filename, stored_path, plates: JSON, uploaded_at`.
Library index fields (filesystem is source of truth; these cache it):
`relative_path, folder, size_bytes, content_hash, mtime: float, missing: bool`.
- `plates`: `[{plate_number, estimated_time(min), filament_g, thumbnail_path}]` (parsed at upload).
- `folder` defaults to `"/"`. `missing` is set by `library_scanner` when the file can't be found.
- File kind is derived from the name (`library_scanner.file_kind`: `3mf` | `stl` | `gcode` | `gcode_3mf`), exposed as `kind`
  in the file dict. A `.gcode.3mf` (Bambu sliced archive) is pre-sliced, not a model: its plates come from
  `Metadata/plate_N.gcode` headers + `Metadata/plate_N.png` (`three_mf_parser.parse_sliced_archive`), no thumbnail regen.

### tags
`id, name (unique), color: str ("#RRGGBB" default "#64748b"), category: str, created_at`.

### file_tags
`file_id FK → uploaded_files (CASCADE), tag_id FK → tags (CASCADE)`. Composite PK.

### orders
`id, order_type` (`customer`|`internal`), `customer, title, due_date?, notes?`, `on_hold: bool`,
`parts: JSON, amount_paid: float?, payment_status: str="unpaid"` (`unpaid|partial|paid`),
`created_at, updated_at`.
- `parts`: BoM checklist `[{id, name, qty, material, est_minutes, filament_id?, filament_color?}]`. No
  per-part fulfillment tracking. **Derived (not stored)**: `status` (hold if on_hold; else queued/
  in_progress/complete from linked jobs), `progress` (completed/active jobs, 0..1), `job_count`,
  `filament_cost_total` (sum of `jobs.filament_cost` across the order's non-cancelled jobs, or `null`).
- **Orders are internal-only (BIZ-186).** Customer sales and payments are recorded as *projects* (customer
  pages + the financial summary read those). `POST /orders` with `order_type="customer"` and `PATCH`ing an order
  *into* a customer order are 422; so is `POST /jobs` with an `order_id` of a customer order. Migration v032
  converted every existing customer order that had no linked project into a project (name=title, same
  customer/amount/status/due/hold, `amount_paid` → one opening payment, customer account linked when exactly one
  matches the name, `parts` kept as text in `notes`, `projects.converted_from_order_id` = provenance) and
  re-pointed its jobs; the order row stays as the project's internal job grouping — nothing is deleted. Legacy
  customer orders already linked to a project were left alone. `amount_paid`/`payment_status` on orders are
  historical and no longer feed reporting.
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
- `POST /api/v1/jobs/{id}/complete-manually`: slices for a chosen printer (real slice, isolated
  directory, same pattern as `verify-slice`) then marks the job `complete` without ever printing it -
  sets `actual_filament_grams`/`actual_seconds` from the real slice, deducts Spoolman filament, and
  updates the printer's `awaiting_plate_clear`/lifetime counters as if it had really finished, all
  without sending anything to the printer connection. Fires no webhooks/notifications either way. Works
  from any non-terminal status, including `printing`/`uploading`; touches no job state until the slice succeeds (a failed slice returns 422 and leaves the job exactly as it was), and a second concurrent call for the same job gets 409. See
  `docs/superpowers/specs/2026-09-15-manual-job-completion-design.md`.

**Actual values** (set at production slice time, before the `gcode_files` row is deleted):
`actual_filament_grams: float?, actual_seconds: int?, actual_filament_breakdown: JSON?,
deduction_skipped: bool?`. `deduction_skipped` is set `True` when the queue engine can't confidently
deduct consumed filament from Spoolman (e.g. no matched spool) — see `queue_engine.py` around where
`lifetime_print_seconds` is accrued.

**Estimate values** (set by an optional background test-slice, gated by `queue_config.estimates_enabled`):
`estimate_token: int=0, estimate_status: str?` (`pending|done|failed|null`), `estimate_seconds: int?,
estimate_filament_grams: float?, estimate_filament_breakdown: JSON?, estimate_preset_label: JSON?`.

`printed_on_printer_id: int?` (v025, plain integer — no FK; `delete_printer` nulls it) — the printer the job ran on, set when it enters `printing` (and by complete-manually), never cleared; unlike `assigned_printer_id` (nulled on fail/cancel) it lets fleet analytics attribute failures. Analytics falls back to `assigned_printer_id` for pre-v025 rows.
`not_before: str?` (v028) — UTC ISO instant before which the queue engine won't start the job (null = ASAP). The claim and resume-sliced queries skip a not-yet-due job (it does **not** block the head of the line — jobs behind it run), offline printers don't pre-slice it, and the engine's idle wait is capped at the earliest future `not_before` (`_seconds_until_next_schedule`) so it starts on time. Set on `POST /jobs {not_before}` or `PATCH /jobs/{id}/schedule` (queued/blocked only, else 409; null clears + wakes the engine).

`filament_cost: float?` — manually-entered cost of the filament used for this job (never computed from
Spoolman pricing), for future profit/loss reporting. Set via `PATCH /api/v1/jobs/{id}/cost`; not touched
by any other route. Summed (non-null values only) into `filament_cost_total` on the linked order
(`orders.py::_derive`) and on the linked project (`projects.py::_project_progress`).
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
`filament_map?` (JSON, nullable), `slice_failed: bool, slice_error: text?`,
`model_target_id?` (v031; set on rows materialized from a `job_model_targets` row, null = explicit pick).
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

### sliced_versions  (v033 — slicing cache, BIZ-189)
A cached slice: library file `file_id` (a `.gcode` / `.gcode.3mf`, UNIQUE, CASCADE) is what model `source_file_id`
(SET NULL — the version then stands alone) sliced to. Key fields: `source_content_hash, plate_number, machine_preset,
process_preset, filament_presets: JSON[str] (ordered), extra_config: JSON (bed type + job overrides, exactly the
`SliceRequest.extra_config`), tool_index?, filament_map?, artifact_kind (gcode|gcode_3mf)` → `cache_key` = sha256 of
their canonical JSON (`services/slice_cache.cache_key`; filament colour is deliberately **not** in it). Non-key:
`preset_content_hash?` (sha256 of the sidecar's merged config for those presets) + `slicer_version?` (Laminus
`/api/health` `orca_version`) → **stale** when either differs now (`slice_cache.staleness`; unknown when the sidecar is
unreachable); `filament_type/color` (display + default ask), `estimated_seconds, filament_grams, filament_breakdown?`,
`created_from_job_id?` (plain int), `created_at`. The display name is the library file's name.
The link lives only in the DB (the filesystem stays the source of truth for the files themselves): a cached file moved
by hand keeps its row/version (hash-matched move), deleted by hand goes `missing` (excluded from lookups) and comes
back if it reappears, **edited** by hand (hash changes on rescan) is detached (`source_file_id` NULL, logged
`event=version_detached`), and a gcode dropped in by hand or a rebuilt DB has no version link. Recovering a version
from OrcaSlicer's `; CONFIG_BLOCK_START` header was considered and not done: the header names presets but not the
model bytes it was sliced from, so the key (which hashes the source model) can't be rebuilt reliably.
Raw `.gcode` thumbnails come from the embedded `; thumbnail begin WxH` PNG (largest), else the source model's plate
thumbnail (file dict only).

Job columns (v033): `save_slice: bool`, `save_slice_name?` (save this job's production slice as a version —
`services/slice_saver.py` copies the artifact next to the model as a normal library file + a `sliced_versions` row; a
same-key version for that model is never duplicated; failures are logged/recorded, never fail the job),
`allow_cached_slice: bool` (print a matching version instead of slicing when claimed — `queue_engine._use_cached_slice`
builds the key from the exact `SliceRequest`, takes the newest present same-key version for that model, reslices a stale one
unless the policy pins it, stages a private copy; any lookup error just slices), `sliced_version_id?` (plain int —
the version it printed), `slice_cache_info: JSON?` (latest decision `{decision: hit|miss, reason?, at, cache_key,
source_content_hash, sliced_version_id?, cached_file_id?, cached_file_hash?, preset_content_hash_stored/current?,
slicer_version_stored/current?, stale?, stale_reasons, policy: use_latest|pin_cached, gate?: laminus_down (claimed on this
version while Laminus was down — BIZ-201), save?: {outcome:
saved|duplicate|failed, sliced_version_id?, cache_key, file_id?, error?, at}}`). `uploaded_files.pack_recipe_hash?`
(project packs). `queue_config.slice_cache_use_latest_settings: bool = True` (on: automatic reuse reslices a stale
version; off: it still prints, flagged stale). Every decision also logs one line `slice_cache event=<lookup|
hit_slice_skipped|miss|saved|save_duplicate_skipped|save_failed|pack_reused|pack_new> key=value …` on logger
`app.services.slice_cache` (`grep slice_cache` in `docker compose logs themis`).

### gcode_files
`id, job_id FK, printer_id FK, path, filament_grams: float?, estimated_seconds: int?`.
- `filament_grams` / `estimated_seconds`: parsed from the gcode header after slice completes (OrcaSlicer
  emits `; filament used [g] = X` and `; estimated printing time = Xh Xm Xs`).
  Exposed on `GET /api/v1/jobs/{id}/details` as `filament_grams` / `estimated_seconds`.
  Aggregated per-project in the project dict as `filament_grams` / `estimated_seconds`.
  Row deleted when print completes or job is cancelled.
- `slice_inputs: JSON?` (v033) — the slicing-cache key inputs this artifact was sliced from (null for pre-sliced files /
  uncacheable sources), so a job flagged "save" after slicing can still be saved.

### job_model_targets  (v031 — "any printer of this make/model")
`id, job_id FK (CASCADE), machine_profile` (a printer's make/model = its `current_orca_printer_profile`),
`print_profile, filament_profile?, filament_id?, filament_type, filament_color` (`"any"` default),
`filament_map?` (never slot-pinned: `tool_index` is rejected, slots differ per printer).
Persistent intent; `services/model_targets.py` **materializes** it into per-printer `job_printer_configs`
rows (at create/PATCH via `materialize_job`, and every queue cycle in `_try_claim_for_printer` via
`sync_targets_for_printer`), so the claim query / slicer / estimates keep reading configs by (job, printer).
Sync adds rows for printers added or re-profiled later and removes rows for printers that no longer match —
only for `queued`/`blocked` jobs. An explicit per-printer config wins over a target for the same printer.
`slice_failed` stays per printer. Unique `(model_target_id, printer_id)` where not null, and unique `(job_id, printer_id)` on the configs table
(v031 de-duplicates first). A target's `filament_profile` is a real preset or null (never the type/"any"). API: `model_targets`
on `POST /jobs`, `PATCH /jobs/{id}/configs` (either list may be empty, not both), `GET /jobs[/{id}/details]`,
`POST /projects/{id}/generate` (`eligible_machine_profiles`).

### printer_alarms (v030)
`id, printer_id (FK → printers, ON DELETE CASCADE), code, severity ('info'|'warning'|'error'|'fatal'), message, source ('hms'|'klipper'|'sdcp'), help_url?, first_seen, last_seen, resolved_at?, acknowledged_at?`. A row is *active* while the printer keeps reporting `code` (`resolved_at` null); it resolves when the report stops and is kept as history (resolved > 90 d purged at startup). A code that returns is a new row. `acknowledged_at` only silences badges/the unacknowledged list. `queue_config.alarm_min_severity` (default `warning`) filters `printer.alarm` webhooks/notifications. Bambu `hms` severity = `code >> 16` (1 fatal, 2 error, 3 warning, 4 info).

### queue_config / spoolman_config / webhook_config / notification_config
`queue_config{check_interval_minutes:int=5, operator_name:str?, snapshot_interval_seconds:int=2,
estimates_enabled:bool=False, slice_cache_use_latest_settings:bool=True}`. `estimates_enabled` gates the background test-slice estimate pipeline
(see `jobs` § Estimate values above); flipping it off does not clear already-computed estimates.
Managed via `GET/PUT /api/v1/settings/queue`.

`spoolman_config{enabled, url?, api_key?, sync_interval_minutes:int=15, last_sync_at?, last_attempt_at?,
last_sync_error?, last_sync_error_code?, low_stock_default_g?: float, low_stock_overrides?: {filament_id: grams},
low_stock_alerted?: [spool_id] (v026)}`. The low-stock trio drives `spool.low` alerts (`services/spool_alerts.py`; managed via
`GET/PUT /api/v1/spoolman/low-stock`; `low_stock_alerted` is service-written state). Managed via `GET/PUT /api/v1/settings/spoolman`,
`POST /api/v1/settings/spoolman/test`. The last four sync-status fields are written only by
`spoolman_sync.record_sync()` (called by the manual `POST /api/v1/spoolman/sync-now` and by
`spoolman_sync.SpoolmanSyncLoop`'s periodic background sync, paced by `sync_interval_minutes`); a
successful sync always clears `last_sync_error`/`last_sync_error_code`. Read via
`GET /api/v1/spoolman/sync-status` — used by the App shell's status-indicator bubble (green/red/orange
for success/fail/stale, stale = last successful sync more than 2 intervals old) and by the Spoolman
settings page's sync-details panel.

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

### Job costing (v028): cost_config, project_labor, printers.machine_rate_per_hour, project_parts.unit_cost
A project's real cost = **filament** (manually entered `jobs.filament_cost`) + **machine** (each *completed* job's
`actual_seconds` × the rate of the printer it ran on: `printers.machine_rate_per_hour` if set, else the shop rate) +
**labour** (`project_labor.minutes` × shop labour rate; rows: `project_id` CASCADE, `minutes`, `logged_on`, `note?`)
+ **parts** (`project_parts.quantity × unit_cost`, parts with no cost add nothing). `cost_config` is a singleton
(`machine_rate_per_hour`, `labour_rate_per_hour`, default 0) managed at `GET/PUT /api/v1/settings/costs`. Rates are
applied **live**, never snapshotted: changing one re-prices past jobs. `services/job_costs.py` computes it
(`compute`, `costs_by_project`); `GET /projects/{id}` carries `costs {filament, machine, labour, parts, machine_hours,
labour_hours, total}`; customer financial `expenses`/`profit` use the total and each window adds `expense_breakdown`.
Labour log: `/api/v1/projects/{id}/labor` (`routes/labor.py`, `projects:read`/`write`). Machine time uses the slicer's
`actual_seconds` (not measured) and the printer in `assigned_printer_id`.

### project_payments (v024)
`id, project_id FK → projects (CASCADE), amount: float (>0), received_on: "YYYY-MM-DD" (day the money
arrived; not in the future), method: cash|card|bank_transfer|check|other, note?, created_at`. CRUD at
`/api/v1/projects/{id}/payments` (`routes/payments.py`, scopes `projects:read`/`projects:write`); cross-project
history at `GET /api/v1/customers/{id}/payments` (newest first, adds `project_name`). v024 back-fills one
"opening balance" payment per project with `amount_paid > 0`, dated the project's creation day. Customer
financial **revenue is cash-basis** — payments count in the windows containing `received_on`; projects with no
payment rows fall back to their creation date; expenses/billed/outstanding/`project_count` stay bucketed by
project creation date.

### projects
`id, name, customer:str="", order_type:str="internal"` (`"customer"`|`"internal"` — same vocabulary as
`orders.order_type`, but this is the project's own field, not a copy of the linked order's), `on_hold:
bool, due_date?, machine_uuid?, process_uuid?, notes?, result_file_id FK?, order_id FK?, source_app?,
source_user?, source_layout_id?, share_token? (unique), share_token_created_at?, amount_paid: float?,
price: float? (v023), payment_status: str="unpaid"` (`unpaid|partial|paid`), `stage: str="queued"` (`draft|planning|queued`),
`customer_id FK?` (owning customer account), `created_at, updated_at`.
- `stage`: `draft` (customer request; `generate` → 409) → `planning` (staff can generate jobs; queue
  engine won't claim them) → `queued` (jobs claimable). Forward-only via `POST /{id}/promote`. Staff/API
  creates default `queued` (pre-stage behavior); customer portal creates `draft`. Queue filter lives in
  `queue_engine._try_claim_for_printer` (jobs with no project always claimable).
- Full CRUD at `/api/v1/projects`. Created by Themis UI (Project Builder) or by Ordinus
  (`source_app="ordinus"`, `source_layout_id=<ordinus BOM id>`).
- `customer`/`order_type`/`on_hold`/`due_date` are the project's own customer-facing fields (set/edited
  directly via the Project Builder), independent of whether it's linked to an `orders` row.
- `amount_paid`/`payment_status`: **derived from `project_payments`** once a project has any payment row
  (`services/payments.py`: unpaid = nothing received; paid = received ≥ `price`; else partial; no price →
  partial). `PATCH` that *changes* either field → 409 while payments exist (echoing current values is fine); with no payment rows they stay manually
  settable (legacy API clients such as Ordinus, and "marked paid, no amount"). Adding the first payment
  adopts a hand-entered `amount_paid` as an opening payment; creating a project with `amount_paid>0` records
  it as one too; a `price` change re-derives the status; deleting the last payment resets to unpaid/null.
  Independent of the linked order's own copy.
  `filament_cost_total` (derived, not stored — `projects.py::_project_progress`) sums `jobs.filament_cost`
  across the project's jobs, alongside the existing `actual_filament_grams`/`actual_seconds` aggregates.
- `price_visible: bool` / `quote_accepted_at?` (v027): staff-controlled flag for showing the quote in the customer portal (`PATCH /projects/{id} {price_visible}`; the project page has a "Show price to customer" checkbox) and when the customer accepted it (`POST /api/v1/customer/projects/{id}/quote/accept`: idempotent, moves a `draft` to `planning`, 409 with nothing visible; cleared when `price` later changes). The portal's `quote` ({price, paid, balance, accepted_at, payments[{id, received_on, amount, method}]}) is `null` unless `price_visible` and a price exist, and never carries costs, profit, filament spend or payment notes.
- `price` (v023): quoted total. Outstanding balance = `max(price - amount_paid, 0)` unless
  `payment_status == "paid"`; no price → no known balance. Responses also carry derived
  `customer_name` (the linked `customers.name`, or null).
- `order_id`: set by `generate_project` — the internal `orders` row that groups all generated jobs for
  fulfillment tracking. `NULL` until the project is first generated. Not the same thing as the
  project's own `order_type` field above.
- `share_token`/`share_token_created_at`: public share-link state, `NULL` = not shared. Managed via
  `GET`/`PUT`/`DELETE /api/v1/projects/{id}/share` (scope `projects:share`, distinct from
  `projects:write` — see `docs/agent/conventions.md` § Invariants). `PUT` always generates a fresh
  token (create and regenerate are the same operation); `DELETE` clears it (revoke). Read via the
  unauthenticated `GET /api/v1/public/projects/{token}` in `app/api/routes/public.py`, which returns
  exactly: `name, customer, due_date, on_hold, items[{name, quantity, quantity_completed}],
  parts[{name, quantity}], links[{url, label}], jobs_total, jobs_complete,
  estimate_seconds_remaining, updated_at` — nothing else. If a review of `public.py` finds a field in
  its response not in this list, that's a leak, not a stale doc; update this list only when the route's
  own field set intentionally changes.
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
- `customer_id` FK? — set only for customer login sessions (`POST /auth/login`, scopes `["customer"]`,
  30-day expiry). Hidden from `GET /api-keys`; revoked when the customer is disabled or its password
  changes.

### customers
`id, name, email` (unique, stored lowercased), `password_hash` (PBKDF2 — `services/password.py`),
`enabled, created_at, phone?, company?, notes?` (contact fields, v023). Staff-managed via
`/api/v1/customers` (`customers:read`/`customers:write`); no self-signup. Password is optional on create —
without one `password_hash` is `""`, which login always rejects (exposed as `has_password: false`) until
staff set one.

### admin_account
Singleton (id=1), created by migration v022 on first boot: `username="admin", password_hash?`
(PBKDF2, NULL until set), `allow_local_login: bool=true` (keyless local-network access is admin),
`recovery_code_hash?, recovery_code_expires_at?, recovery_attempts` (log-delivered one-time code —
`services/admin_account.py`). `auth.get_admin_account()` creates the row if missing (test DBs).
`api_keys.admin_session` marks admin login sessions (hidden from `GET /api-keys`, revoked on
admin password change/recovery).

### bootstrap_sentinel
**Retired** (bootstrap hatch removed): no ORM model, never read or written by app code. The table is still
created by migration v015 (raw SQL, `id, created_at`) so migration history stays linear; fresh and existing
databases both carry an empty unused table. It used to be a concurrency guard for the old "bootstrap when
`api_keys` is empty" flow of `POST /api-keys`.

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

CLI: `cd backend && python -m app.migrations.migrate up|down` (v001 imports `app.models` so `create_all` sees the tables on a fresh DB; `down` is only reliable for the newest migration — several older `down()`s can't run on SQLite).

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
