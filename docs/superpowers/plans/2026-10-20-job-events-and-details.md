# Job events + generic job-details accessor — implementation plan

Spec: `docs/superpowers/specs/2026-10-20-job-events-and-details-design.md` (read first; this plan only sequences it).
Branch: `feature/job-events-details-spec` (no PR until all tasks are done; then the PR gate in CLAUDE.md applies).
Rule for every task: write the test first, watch it fail, implement, rerun. Assert rows/state, not "didn't raise". Test DB = the `session_factory` fixture (never `:memory:`).

## Verified against code (scoping)

| Fact | Where | Plan impact |
|---|---|---|
| `jobs.id` is a plain `primary_key=True`; the table comes from `Base.metadata.create_all` (v001) — **no AUTOINCREMENT** (only `sliced_versions` has `sqlite_autoincrement`). SQLite reuses `max(rowid)+1` after the highest row is deleted. | `models.py:201`, `migrations/v001_initial.py:59` | **Spec §1 premise is false today.** Task 1 makes it true (migration rebuild). |
| Completion/start are durable: `completion_events.enqueue_job_complete/_started` inside the caller's txn, then `event_hub.wake()`. | `completion_events.py:44-67`, `queue_engine.py:1351,1407` | Reporter reuses these; add `origin` param. |
| Printer->core completion path today: `printer_manager.set_job_complete_callback(queue_engine.handle_print_complete)` keyed by **printer_id**; atomic claim = conditional `UPDATE ... WHERE status='printing'`. | `main.py:143`, `queue_engine.py:1358-1411` | Refactor body into `complete_job(job_id, printer_id, *, origin, details)`; keep `handle_print_complete(printer_id)` as thin adapter. |
| `job.failed` / `job.blocked` are in `CORE_EVENTS` (best-effort) but nothing `hub.publish`es them; callers only fire webhooks/notifications. | `registry.py:16-17`, `queue_engine.py:1189,1217` | Reporter `failed`/`blocked` add the first real `hub.publish` of these. Keep the webhook/notification calls. |
| Envelope is frozen `extra=forbid`; `EventDef.version` checked against `schema_version` in `validate_publication`. | `envelope.py`, `registry.py:55-56` | Bump `job.started`/`job.complete`/`job.failed`/`job.blocked` `EventDef.version` to 2 and accept stored v1 rows (see Task 2). |
| `PluginContext` is a frozen dataclass built by `PluginHost.context_for`; factory gets it only if it declares a 2nd positional param. | `plugins/context.py`, `host.py:218-222` | Add optional fields with `None` defaults so existing construction sites/tests keep working. |
| `PluginManifest` has `defines_events`/`subscribes` + `__post_init__` validation. | `plugins/manifest.py:109-185` | Add `job_fields`, `unsupported_job_fields` + validation. |
| **No code path blocks a job for "no eligible printer".** `_block_job` (`queue_engine.py:793`) only handles per-printer problems and publishes nothing; a job nobody can run just stays `queued`. Creation already 422s with no configs/targets, so the case arises later (printers deleted/disabled/dormant, all configs `slice_failed`). Filament mismatch stays `queued` by design (conventions.md). | `queue_engine.py:640-690,793-810`, `api/routes/jobs.py:172,827` | New task 7. The registry text "no eligible printer" describes behavior that doesn't exist yet. |
| Latest migration is `v053`; registered by import + list in `runner.py` (two long lines). | `migrations/runner.py:4,7` | New migrations `v054`, `v055`. |
| Inventory handlers read `payload.loaded_filaments/actual_grams`, legacy `inventory/deduction_notes`. | `inventory/subscribers.py:44-82` | Task 10 adds `get_details` as first source. |

Invariants respected: blocked-vs-failed (reporter `blocked` never sets `failed`); `awaiting_plate_clear` set on start stays in `_start` path; cancel<->stop untouched (`cancelled` not added); core names no vendor (origin is `printer.plugin_id`, never a literal vendor); a plugin never `hub.publish`es core events.

Doc drift to flag after: none found beyond the AUTOINCREMENT premise (run `themis-docs-sync` at the end for `docs/agent/data-model.md`, `backend.md`).

## Tasks (in order; each ends green and committed)

### 0. Test environment (prerequisite)
- `pip install -e ".[dev]"` in `backend/` currently fails building `paho-mqtt` (no wheel / missing build deps) so pytest is not installed in this container. Fix first (upgrade `pip setuptools wheel`, or install a prebuilt wheel, or the build deps), then run `pytest -q tests/services/test_queue_engine.py` as the baseline. If it cannot be made to work, say so and do not claim any task verified.
- Baseline: record the green/red state of the full suite before task 1 so later failures are attributable.

### 1. `jobs.id` never reused (migration v054)
- Test (`tests/test_v054_migration.py` + `tests/test_models.py`): insert job, delete it, insert another -> distinct ids; fails today. Also: migration on a fixture DB keeps all job rows, FKs (`job_printer_configs`, `gcode_files`, `job_costs`, `job_model_targets`, …) intact, and `sqlite_sequence` seeded to `max(id)`.
- `models.py` `Job.__table_args__ = {"sqlite_autoincrement": True}` (fresh/test DBs via `create_all`).
- `migrations/v054_jobs_autoincrement.py` (`version=54`): if `jobs` DDL lacks `AUTOINCREMENT`: `PRAGMA foreign_keys=OFF`, create `jobs_new` from the current DDL with `INTEGER PRIMARY KEY AUTOINCREMENT`, copy all columns, drop, rename, recreate indexes, `PRAGMA foreign_key_check`, `foreign_keys=ON`. Idempotent. `down()` = no-op (AUTOINCREMENT is harmless). Register in `runner.py`. Read the live DDL from `sqlite_master`; do not hardcode column lists.
- Risk: table rebuild with FKs. Mitigate with the `foreign_key_check` assertion in the test. If the rebuild proves unsafe, fall back to: keep ids as-is and drop spec §1's "never reissued" claim + its test (decision for the user; default is rebuild).

### 2. Envelope `origin` + event versions
- Tests (`tests/eventing/test_envelope_and_registry.py`, `test_hub_durable.py`): `origin` field validates (`core` or non-empty <=64); stored v1 JSON without `origin` deserializes with `origin=None` and is delivered; publishing at old `schema_version=1` for bumped events still accepted when read from outbox (decide: `validate_publication` only checks new publications; delivery path doesn't re-validate version — confirm by test).
- `envelope.py`: `origin: str | None = None` + validator.
- `registry.py` + `definitions.py`: bump the four job events to version 2; `JobCompletePayload`/`JobStartedPayload` unchanged (compat window).
- `hub.py`: publication check — if `as_plugin` is set, `origin` must equal it; `origin="core"` only when `as_plugin is None`. Also the `origin` must be `core` or an installed plugin id (use `get_plugin`).
- Tests: a plugin cannot set `origin="core"`; core-acting publish carries `origin="core"` (assert the stored outbox envelope JSON).
- `docs/events.md`: envelope table gains `origin`; version table 1->2 note.

### 3. Job details storage + `get_details` (core)
- Migration `v055_job_details.py`: `job_details(job_id FK jobs.id ON DELETE CASCADE, field, origin, status, value JSON, updated_at, UNIQUE(job_id, field, origin))`. Model `JobDetail` in `models.py`. Register in `runner.py`. Migration test like `test_v053_migration.py`.
- `app/services/job_details.py`: `UNSUPPORTED` sentinel, `DetailResult` TypedDict, `set_details(session, job_id, origin, details)` (no commit; upsert, `UNSUPPORTED` -> `status=unsupported`), `get_details(job_id, fields, origin=None)` with the spec resolution order (stored -> printer plugin manifest -> catalog -> `unknown_field`; multi-origin: printer origin, then latest `updated_at`; `origin=` filters). Needs the job's printer plugin id: `Printer.plugin_id` via `jobs.assigned_printer_id` (falls back to the origin of any stored row when unassigned).
- Tests (each case from the spec's `get_details` bullets): mixed statuses in one call; manifest `unsupported_job_fields` -> `unsupported` with no row; runtime `UNSUPPORTED` stored+returned; arbitrary field/JSON value round-trips; still readable after delivery and after the printer client forgets the job; two-origin preference (interim); rollback test lands in Task 6.

### 4. Manifest field declarations + catalog + REST
- Tests (`tests/plugins/test_manifest_and_registry.py`, new `tests/test_job_fields_api.py`): `JobFieldDef(name, description, type_hint)` validation (name charset, duplicates, a field both declared and unsupported rejected); catalog adds/removes a plugin's fields on enable/disable; printer plugin missing a minimum field logs a warning and still loads (use `caplog`).
- `plugins/manifest.py`: `JobFieldDef`, `PluginManifest.job_fields`, `unsupported_job_fields` + `__post_init__` checks. Export from `plugins/__init__`.
- `plugins/host.py`: build field catalog from enabled plugins (reuse how event/capability catalogs are built); load-time warning for printer-capability plugins lacking `print_start_time`, `print_end_time`, `result` (constant `PRINTER_MIN_JOB_FIELDS` in core; names are spec data, not vendor names).
- Routes in `api/routes/jobs.py` (check `/{job_id}/details` at ~641 stays unchanged): `GET /jobs/{job_id}/detail-fields?fields=a,b` and `GET /job-fields` (put the catalog route in a core router, not under a plugin). Auth like neighbours (`tests/test_route_auth.py`).
- Regenerate `openapi.json` (`python scripts/export_openapi.py` from repo root); `frontend/src/api/contract.test.ts` should pass; no `response-keys.json` entry (frontend doesn't read them).
- Bundled printer plugins (`plugins/{bambu,elegoo_centauri,snapmaker,mock}/`): declare `job_fields` / `unsupported_job_fields` for the minimum set (+ filament/time fields each can supply). Check each plugin's real capabilities in `docs/printer-interface.md`/`printers.md` — do not guess; unknown -> `unsupported`. Their existing tests + `tests/test_vendor_extraction_boundary.py` stay green.

### 5. After the print starts, only the user releases a job from its printer (spec §2a)
- Boundary = status `printing` (the start commit that also enqueues `job.started`, `queue_engine.py:~1340`). Pre-start paths (slice failure, upload/start error, cache-gate release, missing gcode, boot reset, `sliced` when printer not ready) are **unchanged**.
- Audit: `grep -rn "assigned_printer_id"` across `app/`; list every non-user write that can happen to a job already `printing`. Expected: only the printer-ended-in-failure branch (`~586-590`, sets `failed` and clears the printer). Confirm none else (the completion path already keeps it).
- Change: that branch keeps `assigned_printer_id` (status `failed`, reason unchanged). Check readers that assume failed => unassigned (printer job lookups, `printers.py:~878` stop filter, customer portal, frontend fleet/queue); only touch one if it breaks.
- Reporter (task 6): `report()` never writes `assigned_printer_id`; `failed` from a plugin on a `printing` job keeps the printer.
- Tests (assert rows): (a) printer ends in failure -> job `failed`, `assigned_printer_id` still set, printer not reserved (next queued job claimable once plate cleared); fails today. (b) a plugin `report(..., "failed")` and mid-print `report(..., "blocked")` leave `assigned_printer_id` unchanged. (c) user cancel / requeue / printer stop+delete still release. (d) pre-start slice failure still releases and is rescued on a second printer (regression guard for the unchanged invariant).
- Docs: one line in `docs/agent/conventions.md` (assignment rule) and `docs/agent/backend.md`.

### 6. `JobReporter` (callback) and queue-engine refactor
- Tests (`tests/services/test_job_reporter.py`):
  - reporting on a job whose printer belongs to another plugin raises/rejects; job row + outbox unchanged (assert rows);
  - duplicate `report(complete)` -> exactly one `event_outbox` row (dedup) and one deduction downstream;
  - `complete` persists details + `job.complete` outbox row in one txn; forced failure (patch `set_details` to raise) leaves no `complete` row and no outbox row;
  - `failed` / `blocked` set status per existing semantics (`failed` after slicing only; `blocked` keeps `slice_failed` rules) and `hub.publish` best-effort with `origin`;
  - `started` writes `job.started` durable with `origin`; disabled plugin's reporter is rejected;
  - a manual completion (`api/routes/jobs.py:1248`) carries `origin="core"`;
  - plate gate: after a reporter-driven `started` and `complete`, `printer.awaiting_plate_clear` is still True (row + `printer_manager`), and `_process_queue` claims nothing onto that printer until `POST /printers/{id}/plate-cleared`.
- `app/plugins/job_reporter.py` (core): `class JobReporter(plugin_id, session_factory)`, `async report(job_id, kind, details=None)`. Delegates to `queue_engine`/`completion_events`; imports nothing vendor.
- `queue_engine.py`: extract `complete_job(job_id, printer_id, *, origin, details=None)` from `handle_print_complete` (same atomic claim + gcode cleanup + `enqueue_job_complete`, plus `job_details.set_details` in the same txn); `handle_print_complete(printer_id)` looks up the printing job and the printer's `plugin_id`, then calls it with `origin=plugin_id`. Same pattern for the start path (`:1340-1355`) and `_fail_job_post_slice` / blocked helpers (add `origin` + best-effort `hub.publish`).
- `completion_events.enqueue_job_started/complete`: add `origin` kwarg; callers pass it (`"core"` for manual).
- `plugins/context.py`: add `job_reporter`, `job_details`, `job_details_writer` (all `None` default). `host.context_for` fills them; `job_details_writer.set(job_id, {...})` is a separate small class that commits its own txn with `origin=plugin_id`.
- `job_complete.notices`: prefix `"{origin}: "` when origin set and not `core`; test the rendered text. `_notification_content` in `queue_engine.py:~100` is the formatter.
- Keep payload fields (`loaded_filaments`, `actual_grams`, `actual_seconds`) unchanged (compat window).

### 7. `job.blocked` when a job matches no printer (core-initiated, `origin="core"`; unassigned jobs only)
- Definition (default, say if you want different): a job in `queued`/`blocked` with **no live candidate** = none of its `job_printer_configs` points at a printer that exists, is enabled (plugin not dormant/removed) and has `slice_failed = False`. Filament not loaded is NOT "no match" (stays `queued`, per invariant). Pre-sliced eligibility (`gcode_eligibility`) failures already go through `_block_job` per printer.
- Tests (`tests/services/test_queue_unmatched_block.py`): (a) printer deleted/plugin disabled -> after one queue cycle job is `blocked` with reason starting "no eligible printer", one `job.blocked` published (assert hub subscriber got exactly one envelope, `origin="core"`, `entities.job_id`), webhook + notification fired once; (b) second cycle, same reason -> no second event (the `already` guard); (c) printer back/enabled -> job claimable again (status leaves `blocked`); (d) filament mismatch only -> job stays `queued`, no event; (e) a job with one live and one dead printer is not blocked. Each fails without the change.
- `queue_engine.py`: `_block_job` gets `kind`-agnostic publish: when `not already` and the UPDATE hit, call `hub.publish(job.blocked, origin="core", payload {reason})` after commit, then webhook/notification (`_fire_webhooks`/`_fire_notifications` with the reason). Add a per-cycle sweep (`_block_unmatched_jobs`, one SELECT of queued jobs + their configs/printers, no per-job I/O) called from `_process_queue` before claims. The slice-failure path (`:1182`) and `_release_cache_only_claim` publish through the same helper so every `blocked` transition emits one event.
- `JobBlockedPayload` is not added (best-effort class, free-form payload `{reason}`); document in `docs/events.md`.

### 8. Printer-pushed `blocked` mid-print (optional; spec §2)
- Tests (`tests/services/test_job_reporter.py`, extend): (a) `report(job, "blocked", {"reason": ...})` on a `printing` job leaves `status=printing` and `assigned_printer_id` unchanged (re-read the row), stores `blocked_reason`, publishes one `job.blocked` (`origin=<plugin>`, payload `phase="print"`); (b) same reason again -> no second event; a different reason -> new event; (c) the queue does not claim that job for another printer in the next cycle (job stays out of `_process_queue` candidates); (d) a following `complete`/`failed` still works and clears `blocked_reason`; (e) a printer that never reports blocked changes nothing (existing flow tests stay green); (f) `blocked` from a plugin that doesn't own the job's printer is rejected, row unchanged.
- `job_reporter.report`: branch on current status: `printing` -> notice-only path (no UPDATE, details write + best-effort publish + webhook/notification with the reason); `queued/slicing/blocked` -> queue-rule path. Dedup via the stored `blocked_reason` (compare before write, same txn).
- Shared publish helper from task 7 takes `phase`; `JobBlocked` payload stays free-form (`reason`, `phase`).
- Declare `blocked_reason` as a standard optional printer field in `docs/printer-interface.md` (not in the minimum set; plugins that never push it need not declare or mark it unsupported).

### 9. Plugin test-kit + conformance
- `tests/plugins/conformance.py` (existing kit): add manifest check for the printer minimum field set (declared or unsupported). Test with `dummy_plugin.py` variants (compliant / missing field).
- `tests/services/fake_printer_plugin.py` + `backend/tests/virtual_printers/`: make the fake report through `ctx.job_reporter` in one end-to-end test (`tests/test_workflow_end_to_end.py` style): start -> complete with `print_end_time`/`result` details -> `get_details` returns `ok`.

### 10. Inventory consumer fallback order
- Tests (`tests/services/inventory/…` or `tests/test_spool_deduction_end_to_end.py`): three cases each asserting deducted grams — (a) `get_details(job_id, ["filament_usage"])` present (wins over payload), (b) payload `actual_grams` only, (c) legacy `inventory` payload only. Case (a) must fail without the change.
- `inventory/subscribers.py:deduct_on_complete`: try `job_details.get_details` first; `ok` -> use; `unsupported`/`unavailable`/`unknown_field` -> existing payload path; legacy branch unchanged. Convention for the value shape of `filament_usage`: document in `docs/printer-interface.md`, not enforced.

### 11. Docs
- `docs/events.md` (origin, version bump, reporter-driven publish), `docs/printer-interface.md` (minimum field set, reporter usage, single-writer-per-field rule noted as deferred), `docs/plugins.md` (`job_fields`, `unsupported_job_fields`, `ctx.job_reporter`, `job_details`, `job_details_writer`), `docs/agent/backend.md` + `data-model.md` (new table, service, routes). Then `themis-docs-sync`.

### 12. Verification + review (before any PR)
- Backend: `cd backend && pytest -v` (coverage floor via `pytest -v -ra --cov`; raise `fail_under` if coverage grew). Boundary tests: `test_vendor_extraction_boundary`, `test_provider_boundary`, `test_no_provider_in_core`, `test_plugin_id_boundary`, `test_plugins_do_not_read_environment`.
- Frontend: `cd frontend && npx vitest run` and `npm run build` (contract/openapi only; no UI change).
- Mutation check: for each new behavior test, break the line and confirm red.
- Exactly one fresh non-fork reviewer subagent: give base/head SHA, this plan path, the spec path, `docs/agent/backend-review.md`. Fix findings; write `.claude/review-state.json` only after a genuine clean pass. Then PR into `develop` + auto-merge per CLAUDE.md.

## Out of scope (from spec)
Single-writer enforcement, replay/log UI (BIZ-270), new durability classes, `job.cancelled`, PK swap, cross-instance ID federation, removing the legacy payload fields (follow-up after one release).

## Open decisions (defaults chosen)
1. Task 1 rebuilds `jobs` to get AUTOINCREMENT (default). Alternative: drop the "never reused" claim from spec §1.
2. Bundled printer plugins keep signalling via `printer_manager` callbacks; core adapts them to the reporter using `printer.plugin_id` as origin (no plugin rewrites needed now).
