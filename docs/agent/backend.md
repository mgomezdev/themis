# Backend Reference

FastAPI app at `backend/app/main.py`. Routes under `app/api/routes/`, business logic under
`app/services/`, ORM in `app/models.py`, engine/session/migrate in `app/database.py`, env config in
`app/config.py`. All routers + the `/ws` endpoint are registered in `main.py`; the `lifespan` wires
the three subsystems and connects enabled printers.

## Routes (`app/api/routes/`)

Each module = one `APIRouter(prefix="/api/v1/<x>")`. Endpoints below are the public contract.

13 route modules, all registered in `main.py`.

| Module | Prefix | Key endpoints (method path → purpose) |
|---|---|---|
| `files.py` | `/api/v1/files` | `POST /upload` (store 3MF/STL, parse plates), `GET /{id}/plates`, `GET /{id}/model-filaments` (→ `parse_model_filaments`; returns `[{index,color,type}]`), `GET /{id}/thumbnails/{name}` |
| `jobs.py` | `/api/v1/jobs` | `POST ""` create, `GET ""`/`GET /{id}`, `GET /{id}/details` (full: file/plate/per-printer configs incl. `tool_index`/`filament_map`/assigned/`estimate_*`/`actual_*`; each `printer_configs[]` entry carries `low_stock_warning` — see **Spool preflight & notifications** below), `POST /check-overrides`, `PATCH /{id}/configs` (replace configs + re-queue; persists `tool_index`+`filament_map`), `POST /{id}/unblock` (clear slice_failed + re-queue top), `POST /{id}/cancel` (→ stops printer if running), `GET /{id}/slice-failures` |
| `orders.py` | `/api/v1/orders` | CRUD; list/get carry derived `status`+`progress`+`job_count`; `GET /{id}` adds linked `jobs`; `DELETE` nulls `order_id` on jobs |
| `projects.py` | `/api/v1/projects` | CRUD (`GET ""`/`POST ""`/`GET`/`PATCH`/`DELETE /{id}`), `GET /{id}/jobs`; child resources `items`/`links`/`parts` each get `GET`/`POST /{project_id}/<child>` + `PUT`/`DELETE /{project_id}/<child>/{child_id}` (items also: `PUT /{project_id}/items/reorder`); `POST /{id}/generate` — packs `project_items` into plates (via `project_pack_builder`/sidecar `arrange`), creates the jobs + a linked internal `orders` row. See `data-model.md` § projects/project_items/project_links/project_parts. |
| `printers.py` | `/api/v1/printers` | `GET /types` (vendor descriptors for add-form), `POST ""`/`GET`/`PATCH`/`DELETE /{id}`, `POST /test-connection`, `GET /{id}/profiles` (compatible orca process+filament presets), `GET /orca-machine-catalog`, `POST /rescan-profiles`, `POST /{id}/plate-cleared` (ready-for-work), control: `pause`/`resume`/`stop`(→reconciles job)/`light`/`jog-z`/`fan`/`bed-temp`/`reconnect`, camera: `GET /{id}/camera`(MJPEG), `GET /{id}/snapshot` |
| `queue.py` | `/api/v1/queue` | `GET ""` (active jobs ordered; each job carries `low_stock_warning` — see below, batched: one Spoolman fetch per request, not per job), `PATCH /reorder` |
| `fleet.py` | `/api/v1/fleet` | `GET ""` — per-printer merge of DB row (`enabled,queue_on,awaiting_plate_clear,loaded_filaments`) + live `printer_manager.get_normalized_state` |
| `maintenance.py` | `/api/v1/maintenance` | `GET/POST /items`, `PATCH/DELETE /items/{id}`, `PUT /items/{id}/triggers` (replace-all, not diffed), `GET /templates` (suggested items), `GET /status` (due status per printer × applicable item), `POST /printers/{printer_id}/items/{item_id}/complete` ("mark done") |
| `laminus.py` | `/api/v1/laminus` | Proxies/caches the Laminus sidecar's OrcaSlicer profile catalog: `GET /catalog` (cached), `GET /catalog/status`, `POST /catalog/refresh`, `POST /rescan-and-refresh`, `POST /catalog/confirm-remap` (commits a pending drift-gate remap — see `catalog_utils.compute_drift`). Warmed once at startup (`main.py` lifespan → `warm_catalog_cache`). |
| `settings.py` | `/api/v1/settings` | `GET/PUT /queue` (check interval, operator name, `estimates_enabled`), `GET/PUT /spoolman`, `POST /spoolman/test`, `GET/PUT /webhook`, `GET/PUT /notifications`, `POST /notifications/test`, `GET /fleet-backup` (JSON export of all printers, credentials redacted by default — `?include_credentials=true` to include), `POST /fleet-import` |
| `spoolman.py` | `/api/v1/spoolman` | `GET /filaments`, `GET /spools` (proxy to Spoolman), `PATCH /filaments/{id}` (update `orca_profiles` extra field) |
| `tags.py` | `/api/v1/tags` | `GET ""`, `POST ""`, `PATCH /{id}`, `DELETE /{id}`, `POST /files/{file_id}/assign`, `POST /files/{file_id}/unassign` |
| `api_keys.py` | `/api/v1/api-keys` | `GET ""` (list, never returns hash/raw key), `POST ""` (create — raw key in response **once**; while `api_keys` is empty, bootstrap: ignores requested scopes, grants all of `SCOPES`, guarded by `bootstrap_sentinel` — see `data-model.md`), `POST /{id}/revoke` (soft: `enabled=False`+`revoked_at`), `DELETE /{id}` (hard). Revoking/deleting the last enabled key holding `apikeys:write` → `400`. |

### Spool preflight & notifications

`spool_check.check_spool_sufficiency(needed_g, spool)` is the pure comparison (no DB/HTTP) behind
`low_stock_warning` on both `jobs.py` and `queue.py` above — each route resolves its own
(config→printer→loaded-slot→Spoolman spool) chain and calls it; see `backend-review.md` §6 before
adding a third call site instead of extracting the shared batching logic. `notification_service.py`
(ntfy/Discord/email) is fired alongside `webhook_service` from `queue_engine._fire_webhooks`/
`_fire_notifications` on the same three job events — see `data-model.md`'s `notification_config`
section for the config shape and the empty-events-list semantics gotcha.

Pattern for a route: define Pydantic `*Create`/`*Patch` models, a `_to_dict(row)` serializer, a
`_get_or_404`, use `session: AsyncSession = Depends(get_session)`. `HTTPException(404, "msg")` uses
**positional** detail here (match existing style). Register the router in `main.py`. Every route
(new or existing) needs `dependencies=[Depends(require_scope("<scope>"))]` — see **Auth** below.

## Auth (`app/auth.py`)

`SCOPES: set[str]` — fixed, hardcoded registry, one `read`/`write` pair per route module above (plus
`printers:control`, `apikeys:{read,write}`). `require_scope(scope)` — FastAPI dependency factory; while
`api_keys` is empty, every request passes through unauthenticated (bootstrap hatch, closes permanently
once any key is created); otherwise resolves `X-Api-Key` header or `?key=` query param → prefix lookup
→ hash compare (`services/api_key_service.py`: `generate_key()`→`(raw, prefix)`, `hash_key(raw)`→sha256
hex) → 401 if missing/invalid, 403 if the key lacks the required scope. `require_any_key` — same
resolution, no specific scope check; used by `/ws` (`app/api/websocket.py` resolves it manually before
`websocket.accept()`, since a normal `Depends` chain doesn't apply to websocket handlers — closes with
code `4401` on failure). `last_used_at` is touched on successful resolution, throttled to roughly
once/minute per key (not written on every request). OpenAPI's `/docs` Authorize button is wired via an
`APIKeyHeader(name="X-Api-Key")` security scheme attached in `main.py` (documentation ergonomics only —
enforcement is entirely `require_scope`). `THEMIS_BOOTSTRAP_KEY` env var, checked in `_resolve_raw_key`
before any DB lookup — if set, that exact value is always accepted as a full-scope key via
`secrets.compare_digest`. Recovery path for a fully locked-out install; doesn't require an `api_keys` row
to exist.

## Services (`app/services/`)

**Slicing moved to a sidecar as of the 2026-06-23 Orca-sidecar migration** (see
`docs/superpowers/specs/2026-06-23-orca-sidecar-integration-design.md`) — `slicer_service.py` now
delegates the entire slice to a separate Laminus process over HTTP instead of invoking OrcaSlicer
locally. `preset_resolver.py`, `profile_index.py`, and `project_config_builder.py` implemented the old
local pipeline and have **no remaining callers** — dead code, not yet deleted. `mesh_3mf_builder.py`
is mostly dead the same way; only `source_has_project_settings` still has a live caller. Don't extend
any of the four; extend the sidecar-facing path (`slicer_service.py`/`laminus_sidecar_client.py`)
instead. `thumbnail_regen.py` is the one place that still shells out to OrcaSlicer directly — a
narrower, separate use case (regenerating a thumbnail, not slicing) that the sidecar migration didn't
touch.

| File | Responsibility / key symbols |
|---|---|
| `abstract_printer_client.py` | `AbstractPrinterClient` ABC, `ConnectionField`, `PrinterCapabilities`, `StartPrintOptions`, `PrinterFile`. `remap_sliceable_3mf(sliceable_3mf, *, tool_index, filament_map)` — default no-op; `SnapmakerExtendedClient` overrides → `snapmaker/remap.remap_3mf`; Bambu/Elegoo inherit the no-op. See `printers.md`. |
| `printer_client_factory.py` | `REGISTRY` (printer_type→class path), `get_printer_types_for_ui()`, `create_client(printer)` / `create_client_from_config(type,cfg)` (pass only `connection_fields()` keys to the ctor). Add a vendor here. |
| `mock_printer_client.py` | `printer_type="mock"` — always-connected fake driver for E2E/integration tests; accepts uploads/print commands without touching the network. Registered in `REGISTRY` like any real vendor. |
| `printer_manager.py` | Singleton `printer_manager`. `_clients: {id: client}`. `_STATUS_SERIALIZERS` (per type → normalized fleet dict). `is_printer_ready` = `client.is_idle AND id not in _awaiting_plate_clear`. `set/is_awaiting_plate_clear`. `connect_printer` wires `_on_state_change`/`_on_print_complete`/`_on_ams_change`. `on_print_complete` sets awaiting + broadcasts `plate_clear_required`. `on_ams_change` syncs AMS trays → DB `loaded_filaments`. `get_normalized_state(id)` = serializer + capabilities + awaiting. **Holds its own `SessionLocal` reference, bypassing per-request DI** — see `backend-review.md` §4. |
| `queue_engine.py` | Singleton `queue_engine`. `wake()` sets the event. `_process_queue` claims; `_try_claim_for_printer` (head-of-line: filament/slice mismatch ⇒ `_block_job`, no skip-ahead). `_run_slice_and_print` (thread-pooled slice → upload → start; sets `awaiting_plate_clear=True` on `status=printing`; builds `ams_mapping` from matched tray). `_slot_for_config(config, loaded)` (selects the slot to slice with: `tool_index` → `loaded[tool_index]` directly; else delegates to `_matching_loaded_filament` type+color match). `_matching_loaded_filament(config, loaded)` (type+color match; no requirement ⇒ first slot). `_mapped_tools_loaded(filament_map, loaded)` (multi-material gate: returns `True` iff every mapped `tool_index` has a loaded slot). `_filament_mismatch(config, loaded)` (dispatches: if `filament_map` set → `_mapped_tools_loaded`; else → `_matching_loaded_filament`). `_handle_slice_failure` (mark config.slice_failed, block or fail-if-exhausted). `_run_slice_and_print` binds `prepare_hook = lambda p: client.remap_sliceable_3mf(p, tool_index=ti, filament_map=fm)` when either is set, passes it in `SliceRequest`. Multi-material: also passes N `filament_presets` (loaded slots ordered by tool index) into `SliceRequest`. `spawn_estimate`/`run_estimate`/`_do_run_estimate`: background test-slice pipeline for `jobs.estimate_*` (see `data-model.md`). `_fire_webhooks`/`_fire_notifications`: fired (via `asyncio.create_task`, never awaited directly — see `backend-review.md` §3) on `job.complete`/`job.failed`/`job.blocked`. |
| `slicer_service.py` | `SlicerService.slice(SliceRequest) -> path` — delegates the **entire slice** to the Laminus sidecar (`laminus_sidecar_client.LaminusSidecarClient`); Themis does no local profile resolution or 3MF assembly for production slicing. `SliceRequest` fields: `source_3mf, machine_preset, process_preset, filament_presets, filament_colours, plate_number, export_args, extra_config, prepare_hook: Callable[[Path], None] \| None`. Flow: resolve `machine_preset`/`process_preset`/`filament_presets` names to sidecar-catalog UUIDs → if `prepare_hook` is set, copy `source_3mf` into a **job-scoped copy** in `out_dir` and run the hook on *that copy* (never the original — `source_3mf` is the shared library file every job/printer referencing that upload resolves to; see the fixed `prepare_hook`-corrupts-shared-file regression in the git log) → `client.slice_start(...)` → poll → download artifact. `extra_config` carries bed-type/job-level overrides merged by the sidecar after profile resolution. |
| `laminus_sidecar_client.py` | `LaminusSidecarClient` — synchronous `httpx` client for the Laminus sidecar (sync because `SlicerService` runs inside a `ThreadPoolExecutor`; async callers must `asyncio.to_thread` it). Key methods: `health()`, `slice_start(...)`/`slice_prepared(...)`, `poll_status(job_id)`, `download(job_id, dest)`, `get_catalog()` (profile catalog, cached Themis-side — see `laminus.py` route), `get_merged_config(...)`, `arrange(...)`/`pack_stls(...)`/`pack_stls_by_uuid(...)` (multi-part plate packing, used by `projects.py`'s `generate_project`). Raises `SidecarError`. |
| `catalog_utils.py` | `catalog_name_sets(catalog)`, `compute_drift(...)` — detects when the sidecar's profile catalog has renamed/removed a preset that existing printers/jobs still reference ("drift"); backs `laminus.py`'s refresh/confirm-remap flow. |
| `preset_resolver.py` **(dead — no callers)** | `PresetResolver`: resolved OrcaSlicer inheritance-diff presets → flat config for the pre-sidecar local pipeline. |
| `profile_index.py` **(dead — no callers)** | `ProfileIndex`: cached catalog of real machine presets for the pre-sidecar local pipeline. |
| `project_config_builder.py` **(dead — no callers)** | `build_project_config(...)` — built the merged config embedded in a sliceable 3MF for the pre-sidecar local pipeline. |
| `mesh_3mf_builder.py` **(mostly dead)** | `build_sliceable_3mf`/`stl_to_3mf` have no remaining callers (pre-sidecar pipeline). `source_has_project_settings` is still live — used by `override_inspector.py` to detect baked-in settings on an uploaded 3MF. |
| `project_pack_builder.py` | Builds a combined multi-material 3MF from a project's items for the sidecar's `arrange` step: one `<object>` per unique `(file_id, slot_index)` with N `<item>` refs (N=quantity, so OrcaSlicer treats each copy as an independent packable part), per-object extruder assignment in `model_settings.config`, filament arrays + bed dims in `project_settings.config`. Used by `projects.py`'s `generate_project`. |
| `override_inspector.py` | `inspect_overrides(...)` — diffs settings baked into an uploaded 3MF vs chosen presets (New Job "won't carry over" warning). |
| `three_mf_parser.py` | Parse plates/metadata from an uploaded 3MF. `parse_model_filaments(path)` → reads `project_settings.config` `filament_colour`/`filament_type` → `[{index(1-based), color, type}]`. |
| `snapmaker/paint_remap.py` | OrcaSlicer `TriangleSelector` `paint_color` codec (AGPL-sensitive; isolated in the Snapmaker plugin). Public API: `decode_nodes(hex)`, `encode_nodes(node)`, `remap_paint_color(hex, mapping)`. Codec: nibbles right-to-left; bits LSB-first per nibble; 2-bit `split_sides`; leaf `code==3` = 4-bit nibble for states ≥ 3; state s ≥ 3 → filament `s−2` (1-based). `remap_paint_color` swaps every filament leaf per `{filament(1-based): tool_index(0-based)}` mapping; byte-exact round-trip. |
| `snapmaker/remap.py` | `remap_3mf(prepared_3mf, *, tool_index, filament_map)` — rewrites a prepared 3MF in-place (atomic temp-file swap). `tool_index` path: sets all object `extruder` metadata to `tool_index+1`. `filament_map` path: (1) rewrites `paint_color` attrs in all `3D/*.model` via `remap_paint_color`; (2) patches object `extruder` metadata via `_patch_model_settings_filament_map`. Both `None`/empty → no-op. Called exclusively via `SnapmakerExtendedClient.remap_sliceable_3mf`. |
| `camera_proxy.py` | `grab_jpeg_frame`, `stream_mjpeg`, `stream_rtsp_ffmpeg` (RTSP→MJPEG via ffmpeg). |
| `spoolman_service.py` | Spoolman HTTP client: `fetch_filaments`, `fetch_spools`, `test_connection`, `patch_filament(url, api_key, filament_id, orca_profiles)` — writes OrcaSlicer profile mappings into the Spoolman filament's `extra.orca_profiles` field (double-JSON-encoded to satisfy Spoolman text-field constraints). |
| `spool_check.py` | `check_spool_sufficiency(needed_g, spool)` — pure comparison behind the low-stock preflight warning (no DB/HTTP; callers resolve the spool dict and needed-grams first). See **Spool preflight & notifications** above. |
| `webhook_service.py` | `schedule(url, secret, event, job_id, extra)` — fire-and-forget (`loop.create_task(fire(...))`, never awaited by the caller), signs the payload (`X-Webhook-Signature: sha256=<hmac>`), POSTs. The established pattern any new async delivery mechanism should copy — see `backend-review.md` §3. |
| `notification_service.py` | `send_ntfy`/`send_discord`/`send_email` — each fire-and-forget, never raises (swallows its own exception, returns an error string instead). `dispatch(cfg, event, job_id, title, message)` fans out to every enabled channel whose own `*_events` list contains `event`. See `data-model.md`'s `notification_config` for the config shape. |
| `maintenance_service.py` | Maintenance-item due-status computation (`compute_due_status`, `_trigger_due` — `any()` across a item's triggers) and printer vendor/model resolution against the OrcaSlicer catalog (for scoping `"model"`-scope items). See `data-model.md` § maintenance_items/maintenance_triggers/printer_maintenance_state. |
| `thumbnail_regen.py` | Regenerates a 3MF plate's thumbnail by shelling out to OrcaSlicer headless directly (`--slice N --arrange 0 --export-3mf`, not through the sidecar) and extracting the baked thumbnail into the filecache. Runs as a `BackgroundTask` after every library upload, replacing stale thumbnails from third-party tools. **Imports `SessionLocal` directly, bypassing per-request DI** — see `backend-review.md` §4. |
| `library_scanner.py` | Scans the uploads directory; updates `uploaded_files` rows (`relative_path`, `folder`, `size_bytes`, `content_hash`, `mtime`, `missing`). Filesystem is source of truth; DB caches the index. |
| `api_key_service.py` | `generate_key() -> (raw, prefix)` (`raw` = `"thm_" + secrets.token_urlsafe(24)`, shown to the user once), `hash_key(raw) -> str` (sha256 hex — no bcrypt/argon2, these are high-entropy random tokens not human passwords). |

## Key flows (where to change behavior)

- **Claim eligibility** → `printer_manager.is_printer_ready` + `queue_engine._try_claim_for_printer`. Gate add/changes here.
- **Filament gating / AMS mapping** → `_slot_for_config` (entry point; dispatches to `_matching_loaded_filament` or direct `tool_index` lookup) + the slot dict shape (`type,color,filament_profile,ams_tray_id`). DB `printer.loaded_filaments` is the source (AMS auto-synced via `on_ams_change`). Multi-material: `_mapped_tools_loaded` / `_filament_mismatch` gate on all mapped tools loaded.
- **Multi-material remap** → `client.remap_sliceable_3mf` (bound as `SliceRequest.prepare_hook`) → `snapmaker/remap.remap_3mf` + `snapmaker/paint_remap.remap_paint_color`. Change remap logic there for Snapmaker; other vendors override `remap_sliceable_3mf` independently.
- **Slice → print** → `_run_slice_and_print`. Actual slicing happens off-process in the Laminus sidecar (`slicer_service.py` → `laminus_sidecar_client.py`), not locally — see the Services table above. `client.orca_export_args` decides artifact format; `start_print(opts)` with `ams_mapping` for AMS vendors.
- **Background estimates** → `queue_engine.spawn_estimate`/`run_estimate`, gated by `queue_config.estimates_enabled`. Writes `jobs.estimate_*` guarded by an `estimate_token` (bumped on every re-request) so a stale in-flight estimate can never clobber a newer one.
- **Spool low-stock preflight** → `spool_check.check_spool_sufficiency`, called from both `jobs.py` and `queue.py` — see **Spool preflight & notifications** in the Routes section above.
- **Plate-clear / ready-for-work** → set on `status=printing` in `_run_slice_and_print`; cleared by `/plate-cleared`. `is_printer_ready` reads it.
- **Cancel ↔ stop** → `jobs.cancel_job` stops the assigned printer (`asyncio.to_thread(client.stop_print)`) when status in `{printing,paused,uploading}`; `printers.stop_printer` reconciles the running job → `cancelled`.

## Database & config

- `database.py`: async engine (SQLite WAL), `SessionLocal`, `get_session` dep, `init_db()` =
  `create_all` + `migrations/runner.py` (Flyway-style versioned files, `v00N_*.py`, tracked in
  `schema_migrations`). New tables ride on `create_all`; a column added to an *existing* table needs
  its own migration file — see `data-model.md` § Migrations for the exact steps.
- `config.py`: `get_data_dir`, `get_orca_config_dir`, `get_orca_executable`, `get_ffmpeg_executable`.
  Defaults are **platform-aware** (Windows local dev resolves `%APPDATA%\OrcaSlicer` and the Program
  Files `orca-slicer.exe`); env vars `THEMIS_DATA_DIR`/`ORCA_CONFIG_DIR`/`ORCA_EXECUTABLE`/
  `FFMPEG_EXECUTABLE` override.

## Tests

`backend/tests/` (pytest-asyncio). `conftest.py` `client` fixture = httpx + in-memory SQLite +
`get_session` override. Unit tests for services in `tests/services/`. To seed DB state inside an API
test, reuse the override: `agen = app.dependency_overrides[get_session](); session = await agen.__anext__()`.
