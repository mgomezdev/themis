# Test coverage review — 2026-09-29

Scope: backend pytest (817 fns), frontend vitest (306 tests / 42 files), Playwright (7 specs, API mocked).
Method: ran both suites with coverage; AST/regex scans for weak asserts + duplicates; route→test URL matching;
12 mutation spot-checks on critical invariants (mutant applied, suite run, file restored via `git checkout`).
Nothing in the repo was changed except this file.

## 0. Headline numbers

| | Result | Note |
|---|---|---|
| Backend suite | 814 pass / 4 skip, ~70–80 s | 22 s = one test (real network) |
| Backend cov (default `coverage`) | **64 %** | **misleading** — see H1 |
| Backend cov (greenlet-aware) | **76 %** line+branch | jobs.py 31→71 %, orders 74→98 % |
| Frontend suite | 306 pass, ~27 s | |
| Frontend cov | stmts 51.5 / branch 47.5 / funcs 46.8 / lines 54.4 % | |
| Mutants (12) | 6 killed, **6 survived** | §1 |
| Routes with no direct test call | 24 / 142 | static match (incl. `GET /printers/{id}/snapshot`, only a fake route in `test_auth.py`); f-string URLs may hide ≤ a few |

Solid already (don't touch): slicer_service (99 %), notification_service, queue-engine cancel/estimate races,
admin/customer account + auth flows, API-key CRUD, fleet backup redaction, websocket auth, override/filament-map pydantic validation.

## 1. Mutation evidence — invariants NOT pinned

| Mutant | Result |
|---|---|
| start print no longer sets `awaiting_plate_clear` | killed |
| `queue_on` ignored in claim | killed |
| cancel running job no longer stops printer | killed |
| delete-printer active-job guard removed | killed |
| scope check removed in `require_scope` | killed |
| fleet-backup redaction disabled | killed |
| **`POST /printers/{id}/plate-cleared` no longer clears DB flag** | **SURVIVED** |
| **reconcile: FAILED state treated as success** | **SURVIVED** |
| **reconcile: never calls `handle_print_complete`** | **SURVIVED** |
| **upload failure no longer fails the job** | **SURVIVED** |
| **outcome accounting double-counts succeeded qty** | **SURVIVED** |
| **webhook HMAC signature corrupted** | **SURVIVED** |

## 2. P0 — workflow / invariant gaps (add these first)

Each new test: in-memory DB, no sleeps, < 50 ms.

**W1 Ready-for-work gate** (`awaiting_plate_clear`) — M-survivor.
`test_plate_cleared_sets_gate` (`api/test_printers_api.py:233`) patches manager+engine, printer starts at `False`, asserts only the mock call → DB clear unobservable.
`printer_manager.load_awaiting_plate_clear_from_db` (7/11 lines missed), `on_print_complete` (14/16) untested. Frontend: **no test at all** for the "Ready for new work" click.
- B1 seed DB row + manager set `True` → POST → `GET /printers/{id}` shows `False`, `mgr.is_printer_ready(id)` True (real `PrinterManager`, fake idle client), engine wake set. Also 404.
- B2 manager restart: rows `[True, False]` → `load_…_from_db` → set == {id_true}. (Invariant says DB **and** set stay in sync.)
- F1 FleetScreen: card `awaiting_plate_clear=true` shows button; click → `markPlateCleared(id)` + refetch; rejection → error shown, no refetch.

**W2 Print-end reconcile** (missed completion event) — `_reconcile_printing_jobs`, 36/81 lines untested, 2 mutants survived.
Add (fake client, real engine + DB), parametrize where noted:
- idle + normalized `FAILED` → job `failed`, `block_reason` set, `assigned_printer_id` None, `deduction_skipped` True, GcodeFile row **and file** removed, broadcast called.
- idle + other state → `complete` via `handle_print_complete`, lifetime counters bumped; assert `awaiting_plate_clear` is **unchanged** (seed True as print-start would — `handle_print_complete` never sets it; only `_do_upload_and_print` and `PrinterManager.on_print_complete` do).
- parametrize `[disconnected, not idle/printing, paused]` → job untouched.
- `get_normalized_state` raises → job stays `printing`, next job in loop still processed.
- callback already resolved job (race) → no second transition / no double Spoolman deduct.

**W3 Upload / start failure → `failed`** — `_do_upload_and_print` 23/68 missed; patched out in 2 tests; happy path uses `file_upload_supported=False`.
Parametrize `[upload→False, upload raises, start_print→False, start_print raises]` → job `failed`, `block_reason` per case: `"Gcode upload reported failure by printer"` / `"Gcode upload failed: {e}"` / `"Start print reported failure by printer"` / `"Start print failed: {e}"`; printer NOT flagged awaiting-clear. Plus success with `file_upload_supported=True`: `upload_file(bytes, filename)` then `start_print(filename, opts)` with `plate_id`, `ams_mapping=[tray]`. Plus cancelled-during-upload → not set `printing`.

**W4 Queue ordering / head-of-line / no double claim** — no engine test seeds >1 job via `_seed_job`; invariant "head-of-line" has zero pins.
- A blocked (filament mismatch) ahead of runnable B → B stays `queued`, A `blocked`.
- 2 jobs, 2 idle printers → each printer gets one job, none twice, order by `queue_position`.
- 1 job eligible on P1+P2, both idle → exactly one `assigned_printer_id`, one slice task.
- all configs `slice_failed` → assert the intended terminal state (see O9), not just "blocked".
- rescue: P1 slice fails (blocked) → next cycle with P2 ready → claimed by P2; `slice_failed[1]` stays True.

**W5 Project outcome accounting** `PUT /jobs/{id}/outcome` — **0 tests**, 30/65 lines, arithmetic mutant survived. FE `OutcomeModal` + `HistoryScreen` 0 %.
- item qty 3 on plate, failures `[{id,1}]` → completed 2 / failed 1, `job.outcome=='reviewed'`.
- re-PUT with 0 failures → completed 3 / failed 0 (idempotent replace, no double count).
- `quantity_failed` > on-plate clamps; unknown item id ignored; job with no `project_item_quantities` → 400; missing job → 404.
- `GET /jobs/history` (untested, 17 lines): status filter + ordering + empty.
- FE: OutcomeModal submit → `markJobOutcome(id, {failures:[…]})` shape; HistoryScreen list/empty/error.

**W6 Webhook delivery** — `webhook_service` 37 %, signature mutant survived. Add with `respx`/`httpx.MockTransport` (or patch `AsyncClient.post`):
- header `X-Webhook-Signature` == `"sha256=" + hmac(secret, exact body)` computed **independently in the test**; no secret → header absent; body has `event/job_id/timestamp`.
- non-2xx and raised exception → swallowed (logged), no raise.
- `schedule()` returns before delivery completes (fire-and-forget, backend-review §3); no running loop → skipped, no raise.

**W7 App lifespan / SPA** — `main.lifespan` 35/108 missed; SPA block `main.py:184-211` incl. `_within_static_dir` (path-traversal guard) never runs under test (only defined if the static dir exists at import); `_remove_placeholder_printer_from_db` 12/21.
- `with TestClient(app)` w/ tmp `THEMIS_DATA_DIR` + patched sidecar/manager: DB migrated to latest, autoconnect called, engine `start`/`stop` called, shutdown disconnects.
- placeholder cleanup deletes only the named placeholder printer.
- traversal: `/../../etc/passwd`, `//etc/passwd`, `%2e%2e/` → `index.html` (current catch-all behavior), never file contents. Note `serve_spa` also returns `index.html` 200 for unknown `/api/*` paths — decide if a JSON 404 is intended before asserting it. Needs `_within_static_dir` hoisted out of the `if STATIC_DIR.exists()` block (currently untestable without import-time env).

**W8 Auth scope wiring** — default `client` fixture holds an all-scope key, so per-route `require_scope` wiring is mostly unexercised (a few restricted-scope checks exist: `test_customer_accounts.py:80-81`, `test_project_share.py:79-81`, `test_admin_account.py:134-138`; otherwise only a fake `/protected` route).
- one parametrized test walking `app.routes`: every `/api/v1/*` route (allowlist: `public`, `session`, `health`, ws; `customer_portal` uses `require_customer`; `admin_account` nests `require_scope` inside `_admin_only` — walker must recurse into sub-dependencies) has a scope guard whose scope ∈ `SCOPES` (turns conventions.md "mandatory auth" into CI).
- one sampled `401 (no key)` + `403 (key w/ unrelated scope)` per router (~25 requests, <1 s).
- FE↔BE mirror: backend test reads `frontend/src/api/apiKeys.ts`, extracts scope strings, asserts == `auth.SCOPES` (review-doc-listed drift point; 10 lines).

**W9 Cross-layer happy path (never exercised)** — API tests patch `queue_engine`; engine tests seed DB directly; `MockPrinterClient` (built for exactly this, `printer_type="mock"`) has **no tests and no users**.
One integration test: real routes + real `QueueEngine` + real `PrinterManager` + `MockPrinterClient` + fake slicer returning a gcode file:
create mock printer → upload 3MF → `POST /jobs` → one engine cycle → job `printing`, printer `awaiting_plate_clear` → simulate complete → job `complete`, actuals stored → second queued job NOT claimed → `POST plate-cleared` → second job claimed. (~150 ms; replaces need for many mock-only asserts.)

**W10 Migrations chain** — runner 58 %, `rollback_last` 12 lines, `migrate.py` CLI 0 %; v019/v020 no direct tests; only v017 has `down` test.
- one parametrized "each `vNNN.up()` twice on a `create_all` schema is a no-op" (22 params, ms each).
- one upgrade test: legacy-shaped DB with rows → `run_migrations` → `schema_migrations` == 22 ordered versions, rows preserved, second run no-op.
- `rollback_last` on a tail migration with `down`.

## 3. P1 — alternate/error flows & untested routes

Routes with no direct test: `PUT /jobs/{id}/outcome`, `GET /jobs/history`, `POST /jobs/check-overrides`,
`POST /printers/{id}/reconnect`, `GET /printers/orca-machine-catalog`, `GET /files/{id}/model-filaments`,
`POST /files/rescan`, `DELETE /files/{id}/tags/{tag}`, `GET /laminus/catalog`, `POST /laminus/catalog/rescan`,
`GET /spoolman/{filaments,spools,sync-status}`, `DELETE /projects/{id}`, `GET /projects/{id}/{jobs,items,links,parts}`,
`PUT/DELETE /projects/{id}/items/{item}`, `PUT /projects/{id}/items/reorder`, `PUT/DELETE /projects/{id}/links/{link}`.

- **check-overrides** (43/71 missed; documented workflow in `docs/slicing-flow.md`): patched inspector → returns diff list; unknown file 404; non-3MF/bare upload → 200 with empty findings, `has_embedded_settings: False`.
- **Project child CRUD**: one parametrized lifecycle for `items` and `links` (create → update → reorder → delete → 404 on wrong project). `DELETE /projects/{id}`: assert jobs/order link behavior (nulled vs cascaded). `promote` backward → 4xx (forward-only), `generate` while draft → 409 (partly covered via customer flows; add direct).
- **Jobs**: `reorder` demote/back/edge (single job, first-promote no-op); cancel `sliced` job removes GcodeFile row **and** file (route code at jobs.py:530, no test); cancel `slicing`/`uploading` mid-flight.
- **Spoolman**: `spoolman_sync._tick/_loop` 49 %, `spoolman_service` 62 % (HTTP wrappers), 3 GET routes → use existing `tests/spoolman_mock.py`: sync tick records success/error status (`sync-status` route shows it); unreachable → error string via `_describe_error`; deduction on complete already tested.
- **Fleet import**: `fleet_import` 17 lines missed. 400s: invalid JSON, non-object / missing `printers`, `themis_backup_version` < 1 (assert DB unchanged). 200: unknown `printer_type` → skipped + warning, other printers still imported; duplicate names are imported as-is (`Printer.name` not unique — decide if intended).
- **Fleet `GET /fleet`** connected branch (lines 44-48): fake connected client → `state/progress/temps` populated (only offline shape tested).
- **Camera**: `snapshot_camera` 15/29, `camera_proxy` 39 % (`grab_jpeg_frame`, `grab_rtsp_frame`) → patched `httpx`/subprocess: success bytes, timeout → 504/503, no capability.
- **Printers**: `reconnect` (404, 503 on failure, success updates state), `update_printer` branches, `orca-machine-catalog`.
- **Laminus**: `confirm_remap` 42/143 missed (drift/remap is destructive) — each resolution type (rename/remap/drop) applied to jobs+printers+spoolman; partial failure rolls back; `rescan_and_refresh_catalog`, `warm_catalog_cache` (16 lines).
- **Thumbnails**: `thumbnail_regen._render_plate_thumbnail` 27/50 — one test with tiny STL/3MF fixture asserting PNG written + file row updated; renderer error → row unchanged.

## 4. Assertion-quality problems (fix in place)

- **Status-only error tests (84)** assert the code but not "nothing changed". Add one state re-read for the mutating ones:
  `test_reorder_rejects_non_queued_job` (position unchanged), `test_cannot_revoke/delete_last_apikeys_write_key` (key still enabled/present),
  `test_set_job_cost_rejects_negative` (cost unchanged), `test_delete_root_folder_rejected`, `test_upload_rejects_unsupported_type` (no row/file),
  `test_complete_manually_409_*` (job untouched, no printer command), `test_confirm_remap_*_409/422` (pending remap still pending),
  `test_expired_recovery_code_is_rejected` (password unchanged).
- **Implicit-only ("didn't raise") tests**: `test_equal_priority_no_type_error` (assert the claim order), `test_validate_file_id_accepts_normal_filename` (assert the returned value).
- **Source-text tests** (3): `inspect.getsource(...)` + substring → see O2.
- **Sleep-based waits**: 19 `asyncio.sleep(0.05–0.15)` in `test_queue_engine.py` (~2 s + flake risk under CI load). Replace with awaiting the engine's spawned tasks or a `wait_until(pred, timeout)` poll (5 ms step).
- **Whole-singleton mocks**: `patch("…printers.printer_manager")` / `queue_engine` in W1-style tests assert *implementation calls*, not state. Prefer real `PrinterManager` + `MockPrinterClient`.
- **Frontend weak**: `QueueScreen › cancel button calls cancelJob` (`toHaveBeenCalled()` with no id — which job?), `filter chips are clickable` (asserts one chip vanished, not the filtered list), `FleetScreen › ignores non-printer_state WS events` and `ui.test › Progress › renders without crashing` (asserts only that `.progress` exists).

## 5. Duplicates (merge / delete)

| # | Tests | Action |
|---|---|---|
| D1 | `test_printers.py::test_test_connection_known_type_returns_ok_field` (real MQTT attempt, 22 s, asserts `"ok" in body`) ⊂ `test_printers_api.py::test_test_connection_success/unreachable` | **delete**; move `…unknown_type` 422 to api file; fold the rest of `test_printers.py` (loaded_filaments ×4, and `test_list_printers_includes_connected_field` — the only `GET /printers` `connected` test) into `test_printers_api.py`; delete file |
| D2 | camera 404/503/no-capability in `test_camera_stream.py` **and** `test_printers_api.py:283-355` (404 bodies AST-identical) | delete the 3 in `test_printers_api.py` |
| D3 | `test_migrations::test_v012_adds_api_keys_table` ⊂ `…_with_unique_prefix_index`; `services/test_migrate_library.py` re-tests runner adds columns | keep one; replace with W10 parametrized idempotency |
| D4 | "no bootstrap hatch": `test_auth::test_no_key_empty_table_401`, `…nonempty_table_401`, `…deleting_every_key…`, `test_api_keys::test_empty_table_is_not_an_open_door`, `test_websocket::…4401_when_table_empty` | same behavior (no key ⇒ 401 regardless of table). Keep the api-keys one (real app, 3 routes) + ws one; collapse the three in `test_auth` into one param `[empty, other-key, emptied]` |
| D5 | `test_jobs_tool_index.py` + `test_filament_map_plumbing.py::test_printer_config_input_*` | merge into one file |
| D6 | `test_printer_factory.py` tests `create_client_from_config` (unknown type, extra fields ignored) — only partly overlapped (`test_snapmaker_registry.py` calls it once; `test_factory.py` tests `create_client(printer)`). `test_factory::test_registry_has_*` ×3 + `test_get_printer_types_returns_list` ⊂ `…_fields` tests | delete the 4 one-liners; fold the 2 `create_client_from_config` checks into `test_factory.py`, then delete `test_printer_factory.py` (that function backs the test-connection route) |
| D7 | `test_fleet.py::test_fleet_awaiting_plate_clear_field_present` ⊂ offline-state test | add the one assert there, delete |
| D8 | ORM insert/select smoke: `test_models::{create_printer, create_uploaded_file, create_gcode_file}`, `services/test_models_library.py` | every API test exercises these; keep only behavior tests (`share_token` unique, JSON round-trip). (`test_models::test_create_printer` also name-clashes with the API test.) |
| D9 | FE `e2e/smoke.spec.ts` ⊂ `e2e/fleet.spec.ts`; and weaker: regex `/printers online\|Workshop\|Fleet/` matches the **nav label "Fleet"**, passes even if data never loads | delete |
| D10 | helpers re-implemented per file: `_make_3mf` ×9 defs, `_upload_file` ×4, `_create_printer` ×6, `_create_job` ×4, raw-session hack `agen = app.dependency_overrides[get_session]()` ×28 | move to `conftest.py` factories (`make_3mf`, `upload_3mf`, `make_printer`, `make_job`, `db` fixture) |

## 6. Obsolete / dead

- **O1** `test_main_lifespan.py` (both tests): docstring says lifespan cleanup; actually inserts/deletes a `Printer` with SQLAlchemy — never imports `app.main`. Zero coverage of `_remove_placeholder_printer_from_db`. → replace with W7.
- **O2** `inspect.getsource` tests: `test_jobs_tool_index_roundtrip.py` (×2), `test_filament_map_plumbing::test_job_routes_round_trip_filament_map`. Pass if the string sits in a comment; fail on harmless refactor. → one real round-trip: `POST /jobs` with `tool_index` + `filament_map` → `GET /jobs/{id}/details` returns both → `PATCH` configs persists changes. (Multi-material is a headline feature; backend API round-trip is currently untested.)
- **O3** 4 skipped tests (`snapmaker/test_paint_remap.py` ×3, `test_filament_map_e2e.py`) hardcode `C:/Users/mgome/Downloads/Hausdeko…3mf` → **never run in CI or on any other machine**. The 3 `test_paint_remap.py` tests need a synthetic 3MF with `paint_color` attributes built in-test (the 4 blank files in `docs/reference 3mfs/` have none, so they don't work). `test_filament_map_e2e.py` also needs the real OrcaSlicer executable + `scripts/spike_filament_remap` — keep skipped or delete.
- **O4** `BootstrapSentinel` model + v015 test: hatch removed (`v022`, "drop bootstrap hatch"); table unused by app code. Migration stays (history); drop the model and `test_v015_creates_bootstrap_sentinel_table` (or keep only inside W10 param).
- **O5** `app/services/project_pack_builder.py`: 100 stmts, 0 % cov, **no importers**, no tests; `generate_project` now uses sidecar `pack_stls`. `docs/agent/backend.md:19,94` and `README.md:164` still document it. → delete module + those doc references (or test if resurrecting).
- **O6** `services/test_legacy_migration.py`, `test_migrate_library.py`: pre-library upload layout / pre-migration-runner schema. Keep only if that upgrade path is still supported (`migrate_legacy_uploads` 13 lines uncovered on the real path).
- **O7** FE `data/mock.ts` + `mock.test.ts` (only consumer is its own test; production code never imports it), `data/types.test.ts` (`expectTypeOf` is compile-time only: zero runtime assertions under vitest; it is type-checked by `tsc -b` in the CI build, so it can only fail on a type rename — low value, delete if `types.ts` is exercised by consumers), `icons.test.tsx › has all required icons`, `App.test.tsx` (negative check for a removed "Filaments" link).
- **O8** Misleading names: `test_slice_failure_requeues_when_other_printers_available` asserts `blocked` and printer 2 is never ready (no rescue exercised); `test_download_and_model_filaments_work_without_stored_path` only hits `/download`.
- **O9** Doc/behavior drift: CLAUDE.md + `docs/agent/conventions.md` say a job goes `failed` when slicing failed on **all** configs; `_handle_slice_failure` only ever sets `blocked` (no exhausted→`failed` path exists in `queue_engine.py`). Decide intended behavior, pin it (W4), fix the docs (`CLAUDE.md:68`, `conventions.md:7-9`, `docs/agent/backend.md:86`, `docs/agent/recipes.md:87`).
- **O10** module-level `pytestmark = pytest.mark.asyncio` (`test_auth.py:16`) hits the sync `test_projects_share_scope_is_registered` (PytestWarning); asyncio markers are no-ops under `asyncio_mode=auto` everywhere.

## 7. Frontend gaps

Files < 50 % lines: `App.tsx` 13 %, `CustomerPortal` 2 %, `RemapModal` 1 %, `OutcomeModal` 0, `HistoryScreen` 0, `ProjectsScreen` 0, `SpoolmanMappingsPage` 0, `api/laminus` 0, `api/orca` 14, `api/queue` 27, `api/settings` 26, `api/projects` 45, `api/customers` 41, `api/adminAccount` 36, `ProjectBuilderScreen` 41, `ProjectDetailScreen` 42, `NewOrderScreen` 41, `OrdersScreen` 35.

- **F1** plate-cleared (W1).
- **F2** `OutcomeModal`/`HistoryScreen` (W5).
- **F3** `RemapModal` + `SpoolmanMappingsPage`: pending remaps render; Confirm disabled until each required item resolved; submit body `{sync_id, resolutions}` matches what backend `confirm_remap` validates (422 rules); 409 (stale sync) shows message.
- **F4** `CustomerPortal`: list, empty, error, 401 → sign-in. (Only e2e-mocked today.)
- **F5** Project generate flow: `ProjectBuilderScreen` Generate → `generateProject` payload (selected printers, items); 409 draft error shown; `ProjectDetailScreen` promote buttons forward-only.
- **F6** `App.tsx` routing table: staff vs customer role routes, unknown path fallback, `/share/:token` bypasses AuthGate (exists).
- **F7** Alternate flows on big screens: NewJobScreen create → 4xx/5xx shows error and stays editable; QueueScreen cancel/unblock rejection message; Settings save failure; `useQueue` poll error/recovery; `reconnectPrinter` button (no test references it).
- **F8** e2e: all 7 specs mock the API, so no FE↔BE integration in this repo (Concordia job covers cross-service). Add 1 Playwright spec for the operator golden path with mocked API but **asserting captured request bodies** (create job → queue → cancel → plate-cleared), like `new-job.spec.ts` already does for `filament_map`.

## 8. Contract drift (FE ↔ BE) — highest systemic risk per review docs

Most FE-consumed routes (fleet, queue, jobs, printers, projects, files) lack `response_model` (~13 routes have one: settings, spoolman sync-status, project share, fleet-import), so `openapi.json` (drift-checked in CI) doesn't describe response fields; both sides test against their own assumptions.
- **C1** vitest: extract every URL literal from `src/api/*.ts`; assert each (method, path) exists in repo-root `openapi.json`. One test replaces ~40 hand-written "POSTs to /api/v1/…" assertions and catches renamed routes for free.
- **C2** backend key-set tests for the FE-consumed shapes (`/fleet` item, `/queue` job, `/jobs/{id}/details`, `/printers`, `/projects/{id}`, `/files`): `assert FE_KEYS <= set(resp.json())`, where `FE_KEYS` is a small JSON checked in and also imported by the FE mapper tests / `e2e/mock-api.ts` fixtures (so fixtures can't invent fields).
- **C3** scope mirror (W8).

## 9. Hygiene / speed

- **H1 Coverage config missing; CI never measures coverage.** Default coverage.py loses code after SQLAlchemy greenlet switches (64 % vs true 76 %). Add `pytest-cov` to dev deps and
  `[tool.coverage.run] branch=true, source=["app"], concurrency=["greenlet","thread"]`; add a ratchet (`--cov-fail-under=75`) in CI. Frontend: add `@vitest/coverage-v8` config (already a devDep) + threshold.
- **H2** 22 s test (D1) = 27 % of backend wall time. Everything else ≈ 50 s; sleeps ≈ 2 s of that.
- **H3** aiosqlite worker-thread `Event loop is closed` warnings surface in `test_maintenance_service` ×6 / `test_notification_service` ×2, but those files already dispose (or use no DB). The leaking engines are in neighbouring files that create one without `dispose()`: `test_legacy_migration.py`, `test_migrate_library.py`, `test_ams_merge.py`, `test_library_scanner.py`, `test_models_library.py`, one of two in `api/test_websocket.py` — fix there (GC of leaked engines fires the warning in later tests). These warnings can mask real leaks.

## 10. Suggested order (effort ≈ tests added)

1. D1 delete + H1 coverage config + O3 fixture fix (cheap, immediate speed/visibility).
2. W1, W2, W3, W5, W6 (5 invariants where mutants survived; ~25 tests).
3. W9 (one integration test) + W4 + W8 (route walk).
4. O1/O2 replacements, D2–D10 cleanup, weak-assert fixes (§4).
5. W7, W10, §3 route gaps, FE F1–F8, C1–C3.

## 11. Status — hardening pass (PR #66, 2026-09-29)

Everything below is on branch `claude/test-coverage-review-qh5b2o`, one commit per task (sha = `git show <sha>`).
Suites at hand-off (after merging `develop` at 20b0e8b): backend 1161 pass (was 814 + 4 skip; ~51 s, was ~70–80 s), frontend 592 (was 306), Playwright 17 (was 7);
backend coverage 86 % line+branch (greenlet-aware; CI floor `fail_under = 84`), frontend stmts 71.6 / branch 65.1 / funcs 67.8 / lines 74.7
(floors 69 / 63 / 65 / 72). All 12 §1 mutants plus the M5b/M13/M14 variants are now **killed**.

### Findings

| Finding | Status | Commit(s) |
|---|---|---|
| W1 plate-clear gate | done (BE + FE F1) | 68c7864, 19dbef8 |
| W2 reconcile | done | 8799289 |
| W3 upload/start failure | done | c87fa3a |
| W4 queue ordering / head-of-line / no double claim | done; O9 behaviour pinned | 80d1816 |
| W5 outcome accounting + history (BE, FE F2) | done | 26617d4, fe47c15 |
| W6 webhook delivery + HMAC | done | 5bb3e47 |
| W7 lifespan / SPA | done (`_resolve_within` hoisted, `register_spa`) | 4f90866 |
| W8 route-walk scope guard + SCOPES mirror | done | a65673f |
| W9 cross-layer happy path | done | 71a7d5f |
| W10 migrations chain | done (found the fresh-DB CLI bug) | 48cb4ac, a580088 |
| §3 check-overrides / project children / jobs reorder+cancel | done (found the reorder route bug) | 85eca71, 392e1fb, 7e8af92, 1566625 |
| §3 Spoolman / fleet import / fleet GET / camera / printers / laminus / thumbnails | done | ae637cb, ecc07f8, 043b3e1, ed68de9, bf2abff, 88eb909, 03f7a9a |
| §3 files/tags misc | done (found the `?tags=` filter bug) | caf0037, e3103ff |
| §4 status-only error tests | done (state re-read added) | 6499ae9, d9e5f4e |
| §4 implicit-only tests | done: slice-queue order test rewritten to assert order (pins the queue mechanics, not the 0/1/2 constants); `_validate_file_id` accept test parametrized | c2e829a |
| §4 source-text tests | done (see O2) | 4caa0d7 |
| §4 sleeps | done: `wait_until`, no sleeps in test_queue_engine | 2f8c69b, d7aa42e |
| §4 whole-singleton mocks | **partly**: invariant tests (W1, W2, W9) use the real manager/engine; ~55 incidental `patch(...queue_engine)` in route tests remain (they now assert state, not just the mock call) | — |
| §4 frontend weak asserts | done | 10331ef |
| D1–D9 duplicates | done | b2b9508, 211646b, d30841f, 0d0c5a3, 2e4c887, d5d17d6, 043b3e1, 0fdcc87, 2520258 |
| D10 shared factories | done | c508ab1, 0d272e2, bcc7b8f, e252fa9 |
| O1 fake lifespan test | done | 4f90866 |
| O2 `inspect.getsource` tests | done | 4caa0d7 |
| O3 skipped tests | done (synthetic 3MF; dead e2e deleted) | f29c0be |
| O4 / O5 dead code | done | 45d7db0, 89c8d47 |
| O6 legacy migration tests | kept `test_legacy_migration.py` (upgrade path still supported); deleted `test_migrate_library.py` | d30841f |
| O7 dead FE data/tests | done | 21d97f0 |
| O8 misleading names | done (rescue case now really exercised) | 80d1816 |
| O9 failed-vs-blocked doc drift | pinned in tests; **docs left for owner** (CLAUDE.md not edited, see below) | 80d1816 |
| O10 asyncio markers | done | 9b8c044 |
| F1–F2 | done | 19dbef8, fe47c15 |
| F3 RemapModal + SpoolmanMappingsPage | done | 1d228ee |
| F4 CustomerPortal | done | 0c3982a |
| F5 project generate / promote | done (found the unmount bug) | 1a9f586, 2d7b7e0 |
| F6 App routing | done (found the settings title bug) | 8f85dc7, e8e42d5 |
| F7 alternate flows | done (found 4 bugs) | fddf6e9, 5ea28a2, 593da55, 07ac76b |
| F8 e2e golden path | done | c9f0001 |
| C1 URL ⊂ openapi | done | 1367af0 |
| C2 response-key contract | done | fc0777a |
| C3 scope mirror | done | a65673f |
| H1 coverage config + ratchets | done | d999ad6, 3f95706, 6b93a6a, ratchet commit |
| H2 22 s test | done | b2b9508 |
| H3 leaked engines | done; file-backed per-test DB adopted as the shared fixture | 9b8c044, 36d1ca3 |

### Product bugs the new tests found (all fixed, each in its own commit with regression tests)

`migrate up` on a fresh DB (a580088) · project-items reorder route shadowed, always 422 (7e8af92) · `GET /files?tags=` ignored (e3103ff) ·
dead FE hook calling a nonexistent route (32c0e44) · new-project Generate dropped its result/errors (1a9f586) · every Settings sub-page titled
"Job queue" (8f85dc7) · retry after a partial multi-plate failure queued plates twice (fddf6e9) · queue cancel/unblock/reorder failures were silent
(5ea28a2) · refused Print-defaults saves left the unsaved value on screen (593da55) · queue/fleet/orders sockets never reconnected, tabs froze after a
backend restart (07ac76b). One flake root-caused (zip timestamps → dedupe miss, f5362fe).

### Left for the owner (not changed; behaviour pinned where noted)

1. **Open product bug — filament matching** (`queue_engine._matching_loaded_filament`): a half-specified ask (type=PETG, colour=any, or the reverse)
   never matches a loaded slot that has both, so the job blocks forever; only both-specific or both-any match. `_find_slot_for_filament`
   (filament_map path) handles the wildcard correctly. Fix idea: skip the comparison for an empty requirement field.
2. Doc drift for the owner to decide (CLAUDE.md untouched): CLAUDE.md:68, conventions.md:7-9, backend.md, recipes.md say a job goes `failed` when slicing
   fails on all configs; the code only ever sets `blocked`.
3. `migrate down` only reliably rolls back the newest migration (v21/v18/v5 `down()` can't run on SQLite; v9 has none).
4. Fleet import: JSON-valid but malformed input 500s (version string/null, `printers` null, non-object entry); backup omits `bed_x_mm`/`bed_y_mm`/
   `no_snapshots_while_idle`; import is additive (twice = doubled fleet).
5. `PATCH /printers/{id}` with a new `connection_config` doesn't reconnect the live client; a failed reconnect leaves no client.
6. FE `OverrideCheck` omits the backend's optional `error` key, so degraded override checks are invisible.
7. The builder's Generate button doesn't gate on a draft stage → the operator sees the raw 409 JSON (the list and detail screens now do gate it; develop a14cd5c); `Progress` ignores `tone="ok"` (dead prop);
   builder qty input snaps to 1 on clear; `SpoolmanMappingsPage` fetches while Spoolman is disabled; `ReadyForWorkButton` has no catch around `markPlateCleared`.
8. Unknown `/api/*` GETs return `index.html` 200 via the SPA catch-all (not asserted). `GET /fleet` for a connected `mock` printer lacks state keys (test-only type).
9. Settings pages other than Print defaults (webhook, notifications, Spoolman, maintenance, tags, API keys, customers, admin) keep their own
   error handling; only some of their failure paths are tested.
10. From the single review pass (all minor, not changed): `GET /files?tags=` is now server-filtered, so `FilesScreen`'s tag counts become co-occurrence
    counts and a zero-count tag stays clickable (decide whether counts should come from an unfiltered list); `contract.test.ts` scans only `src/api/*.ts`
    (~14 raw URL literals in auth/screens/components are unchecked); the response-keys contract covers top-level keys only (nested `temperatures.*`,
    filament slot keys are not); `fetchStub` installs a global `fetch` stub that nothing unstubs (harmless today, every test re-stubs first);
    `openLiveSocket` retries every 30 s forever after an auth rejection and has no jitter; the generic migration-rollback test can prove a `down`
    removes something and nothing else changes, but not that it removes everything its `up` added.

### After the merge with `develop`

`develop` moved while this branch was open (customer accounts/details, migration v023, `Project.price`/`customer_name`, CustomerPicker, draft-Generate
gating, breadcrumbs). The merge fixed three things the drift exposed: the rollback test pinned "newest migration = v22" (now version-independent),
`contracts/response-keys.json` + its FE type check gained `price`/`customer_name`, and a develop test inserted a job for a nonexistent uploaded file
(the shared test DB now enforces foreign keys, so it seeds the file first).
