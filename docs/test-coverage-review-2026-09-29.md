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
| Routes with no direct test call | 23 / 141 | static match; f-string URLs may hide ≤ a few |

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
- idle + other state → `complete`, printer `awaiting_plate_clear` True, lifetime counters bumped (via `handle_print_complete`).
- parametrize `[disconnected, not idle/printing, paused]` → job untouched.
- `get_normalized_state` raises → job stays `printing`, next job in loop still processed.
- callback already resolved job (race) → no second transition / no double Spoolman deduct.

**W3 Upload / start failure → `failed`** — `_do_upload_and_print` 23/68 missed; patched out in 2 tests; happy path uses `file_upload_supported=False`.
Parametrize `[upload→False, upload raises, start_print→False, start_print raises]` → job `failed`, `block_reason` contains `"Gcode upload failed: …"` / `"Start print reported failure by printer"`, printer NOT flagged awaiting-clear. Plus success with `file_upload_supported=True`: `upload_file(bytes, filename)` then `start_print(filename, opts)` with `plate_id`, `ams_mapping=[tray]`. Plus cancelled-during-upload → not set `printing`.

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
- traversal: `/../../etc/passwd`, `//etc/passwd`, `%2e%2e/` → index.html or 404, never file contents; `/api/nope` → JSON 404 not SPA. Needs `_within_static_dir` hoisted out of the `if STATIC_DIR.exists()` block (currently untestable without import-time env).

**W8 Auth scope wiring** — default `client` fixture holds an all-scope key, so per-route `require_scope` wiring is never exercised; only a fake `/protected` route is.
- one parametrized test walking `app.routes`: every `/api/v1/*` route (allowlist: `public`, `session`, `health`, ws) has a `require_scope` dependency whose scope ∈ `SCOPES` (turns conventions.md "mandatory auth" into CI).
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

- **check-overrides** (43/71 missed; documented workflow in `docs/slicing-flow.md`): patched inspector → returns diff list; unknown file 404; non-3MF → empty/422.
- **Project child CRUD**: one parametrized lifecycle for `items` and `links` (create → update → reorder → delete → 404 on wrong project). `DELETE /projects/{id}`: assert jobs/order link behavior (nulled vs cascaded). `promote` backward → 4xx (forward-only), `generate` while draft → 409 (partly covered via customer flows; add direct).
- **Jobs**: `reorder` demote/back/edge (single job, first-promote no-op); cancel `sliced` job removes GcodeFile row **and** file (route code at jobs.py:530, no test); cancel `slicing`/`uploading` mid-flight.
- **Spoolman**: `spoolman_sync._tick/_loop` 49 %, `spoolman_service` 62 % (HTTP wrappers), 3 GET routes → use existing `tests/spoolman_mock.py`: sync tick records success/error status (`sync-status` route shows it); unreachable → error string via `_describe_error`; deduction on complete already tested.
- **Fleet import**: `fleet_import` 17 lines missed — invalid JSON, unknown `printer_type`, duplicate name, non-object file → 400/422 with report; assert DB unchanged on failure.
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
- **Sleep-based waits**: 21 `asyncio.sleep(0.05–0.15)` in `test_queue_engine.py` (~2.5 s + flake risk under CI load). Replace with awaiting the engine's spawned tasks or a `wait_until(pred, timeout)` poll (5 ms step).
- **Whole-singleton mocks**: `patch("…printers.printer_manager")` / `queue_engine` in W1-style tests assert *implementation calls*, not state. Prefer real `PrinterManager` + `MockPrinterClient`.
- **Frontend weak**: `QueueScreen › cancel button calls cancelJob` (`toHaveBeenCalled()` with no id — which job?), `filter chips are clickable` (asserts one chip vanished, not the filtered list), `FleetScreen › ignores non-printer_state WS events` and `ui.test › renders without crashing` ("didn't crash" only).

## 5. Duplicates (merge / delete)

| # | Tests | Action |
|---|---|---|
| D1 | `test_printers.py::test_test_connection_known_type_returns_ok_field` (real MQTT attempt, 22 s, asserts `"ok" in body`) ⊂ `test_printers_api.py::test_test_connection_success/unreachable` | **delete**; move `…unknown_type` 422 to api file; fold rest of `test_printers.py` (loaded_filaments ×4) into one parametrized round-trip in `test_printers_api.py`; delete file |
| D2 | camera 404/503/no-capability in `test_camera_stream.py` **and** `test_printers_api.py:283-355` (404 bodies AST-identical) | delete the 3 in `test_printers_api.py` |
| D3 | `test_migrations::test_v012_adds_api_keys_table` ⊂ `…_with_unique_prefix_index`; `services/test_migrate_library.py` re-tests runner adds columns | keep one; replace with W10 parametrized idempotency |
| D4 | "no bootstrap hatch": `test_auth::test_no_key_empty_table_401`, `…nonempty_table_401`, `…deleting_every_key…`, `test_api_keys::test_empty_table_is_not_an_open_door`, `test_websocket::…4401_when_table_empty` | same behavior (no key ⇒ 401 regardless of table). Keep the api-keys one (real app, 3 routes) + ws one; collapse the three in `test_auth` into one param `[empty, other-key, emptied]` |
| D5 | `test_jobs_tool_index.py` + `test_filament_map_plumbing.py::test_printer_config_input_*` | merge into one file |
| D6 | `test_printer_factory.py` ⊂ `services/test_factory.py` + `test_snapmaker_registry.py`; `test_factory::test_registry_has_*` ×3 + `test_get_printer_types_returns_list` ⊂ `…_fields` tests | delete file + 4 one-liners; keep negative "unknown type" once |
| D7 | `test_fleet.py::test_fleet_awaiting_plate_clear_field_present` ⊂ offline-state test | add the one assert there, delete |
| D8 | ORM insert/select smoke: `test_models::{create_printer, create_uploaded_file, create_gcode_file}`, `services/test_models_library.py` | every API test exercises these; keep only behavior tests (`share_token` unique, JSON round-trip). (`test_models::test_create_printer` also name-clashes with the API test.) |
| D9 | FE `e2e/smoke.spec.ts` ⊂ `e2e/fleet.spec.ts`; and weaker: regex `/printers online\|Workshop\|Fleet/` matches the **nav label "Fleet"**, passes even if data never loads | delete |
| D10 | helpers re-implemented per file: `_make_3mf` ×6, `_upload_file` ×4, `_create_printer` ×6, `_create_job` ×4, raw-session hack `agen = app.dependency_overrides[get_session]()` ×~50 | move to `conftest.py` factories (`make_3mf`, `upload_3mf`, `make_printer`, `make_job`, `db` fixture) |

## 6. Obsolete / dead

- **O1** `test_main_lifespan.py` (both tests): docstring says lifespan cleanup; actually inserts/deletes a `Printer` with SQLAlchemy — never imports `app.main`. Zero coverage of `_remove_placeholder_printer_from_db`. → replace with W7.
- **O2** `inspect.getsource` tests: `test_jobs_tool_index_roundtrip.py` (×2), `test_filament_map_plumbing::test_job_routes_round_trip_filament_map`. Pass if the string sits in a comment; fail on harmless refactor. → one real round-trip: `POST /jobs` with `tool_index` + `filament_map` → `GET /jobs/{id}/details` returns both → `PATCH` configs persists changes. (Multi-material is a headline feature; backend API round-trip is currently untested.)
- **O3** 4 skipped tests (`snapmaker/test_paint_remap.py` ×3, `test_filament_map_e2e.py`) hardcode `C:/Users/mgome/Downloads/Hausdeko…3mf` → **never run in CI or on any other machine**. Build a tiny synthetic painted 3MF in-test (or use `docs/reference 3mfs/`), else delete.
- **O4** `BootstrapSentinel` model + v015 test: hatch removed (`v022`, "drop bootstrap hatch"); table unused by app code. Migration stays (history); drop the model and `test_v015_creates_bootstrap_sentinel_table` (or keep only inside W10 param).
- **O5** `app/services/project_pack_builder.py`: 100 stmts, 0 % cov, **no importers**, no tests; `generate_project` now uses sidecar `pack_stls`. `docs/agent/backend.md` still documents it. → delete module + doc row (or test if resurrecting).
- **O6** `services/test_legacy_migration.py`, `test_migrate_library.py`: pre-library upload layout / pre-migration-runner schema. Keep only if that upgrade path is still supported (`migrate_legacy_uploads` 13 lines uncovered on the real path).
- **O7** FE `data/mock.ts` + `mock.test.ts` (only consumer is its own test; production code never imports it), `data/types.test.ts` (`expectTypeOf` is compile-time; vitest without `--typecheck` runs **zero** assertions → cannot fail), `icons.test.tsx › has all required icons`, `App.test.tsx` (negative check for a removed "Filaments" link).
- **O8** Misleading names: `test_slice_failure_requeues_when_other_printers_available` asserts `blocked` and printer 2 is never ready (no rescue exercised); `test_download_and_model_filaments_work_without_stored_path` only hits `/download`.
- **O9** Doc/behavior drift: CLAUDE.md + `docs/agent/conventions.md` say a job goes `failed` when slicing failed on **all** configs; `_handle_slice_failure` only ever sets `blocked` (no exhausted→`failed` path exists in `queue_engine.py`). Decide intended behavior, pin it (W4), fix the doc.
- **O10** `@pytest.mark.asyncio` on a sync test (`test_auth.py:286`, PytestWarning); `pytestmark = asyncio` / per-test markers are no-ops under `asyncio_mode=auto`.

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

Routes lack `response_model`, so `openapi.json` (drift-checked in CI) doesn't describe response fields; both sides test against their own assumptions.
- **C1** vitest: extract every URL literal from `src/api/*.ts`; assert each (method, path) exists in repo-root `openapi.json`. One test replaces ~40 hand-written "POSTs to /api/v1/…" assertions and catches renamed routes for free.
- **C2** backend key-set tests for the FE-consumed shapes (`/fleet` item, `/queue` job, `/jobs/{id}/details`, `/printers`, `/projects/{id}`, `/files`): `assert FE_KEYS <= set(resp.json())`, where `FE_KEYS` is a small JSON checked in and also imported by the FE mapper tests / `e2e/mock-api.ts` fixtures (so fixtures can't invent fields).
- **C3** scope mirror (W8).

## 9. Hygiene / speed

- **H1 Coverage config missing; CI never measures coverage.** Default coverage.py loses code after SQLAlchemy greenlet switches (64 % vs true 76 %). Add `pytest-cov` to dev deps and
  `[tool.coverage.run] branch=true, source=["app"], concurrency=["greenlet","thread"]`; add a ratchet (`--cov-fail-under=75`) in CI. Frontend: add `@vitest/coverage-v8` config (already a devDep) + threshold.
- **H2** 22 s test (D1) = 27 % of backend wall time. Everything else ≈ 50 s; sleeps ≈ 2.5 s of that.
- **H3** aiosqlite worker-thread `Event loop is closed` warnings (`test_maintenance_service` ×6, `test_notification_service` ×2): engines/sessions not disposed. Add `await engine.dispose()` to those fixtures; these warnings can mask real leaks.

## 10. Suggested order (effort ≈ tests added)

1. D1 delete + H1 coverage config + O3 fixture fix (cheap, immediate speed/visibility).
2. W1, W2, W3, W5, W6 (5 invariants where mutants survived; ~25 tests).
3. W9 (one integration test) + W4 + W8 (route walk).
4. O1/O2 replacements, D2–D10 cleanup, weak-assert fixes (§4).
5. W7, W10, §3 route gaps, FE F1–F8, C1–C3.
