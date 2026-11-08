# Discovery quick-add + printer "configured" status Implementation Plan

> **For agentic workers:** executed natively in one session (CLAUDE.md "Development workflow"): TDD per task, one reviewer
> subagent over the whole branch at the end, PR gate. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add many printers from one scan, in place, and keep any printer whose setup is incomplete out of the queue until a
connection succeeds.

**Architecture:** A persisted `printers.configured` flag (+ `config_error`) set only by a successful connection, held in DB and in
an in-memory `PrinterManager` set (like `awaiting_plate_clear`), checked by both claim paths. A new core service
`app/services/printer_setup.py` owns the vendor-neutral setup logic (blank stripping, missing-required check, change detection,
the connect-and-classify test, the background auto-test). `DiscoverPrinters` becomes an in-place multi-row quick-add table driven
by plugin-declared `ConnectionField.quick_add` metadata.

**Tech Stack:** FastAPI, async SQLAlchemy/aiosqlite, pytest-asyncio; React/TS/Vite, Vitest + Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-07-discovery-quick-add-design.md` (Linear BIZ-369)

## Global Constraints

- Core never names a vendor or branches on a plugin id; quick-add/required logic reads only `ConnectionField` metadata
  (`test_vendor_extraction_boundary.py`, `test_provider_boundary.py`, `test_no_provider_in_core.py`, `test_plugin_id_boundary.py`,
  `frontend/src/pluginIdGuard.test.ts` stay green).
- Blank connection values are omitted, never stored or sent as `""`.
- Connection attempts never block the event loop or queue loop (blocking connect/probe via executor / `asyncio.to_thread`).
- Identity = (IP, model). Model = `model_uuid` when both sides have one, else the `(plugin_id, manufacturer_id, model_id)` triple.
- Migration `v067_printer_configured`; existing rows back-filled `configured = 1`.
- `openapi.json` regenerated; `contracts/response-keys.json` updated; coverage floors only go up.
- Chip colours from tokens `--ok`, `--warn`, `--err`.
- Concurrency cap 4 for batch create (frontend) and for parallel auto-tests (backend semaphore). Auto-test timeout reuses 15 s
  (`_TEST_CONNECT_POLL_S`).

## Decisions taken here (spec left them open or implied them)

- In-memory unconfigured set lives in `PrinterManager` (`_unconfigured`), loaded at startup; no DB read added to `is_printer_ready`.
  `_try_claim_for_printer` reads `printer.configured` from the row it already loads.
- The auto-test connects the printer's **real** managed client (`printer_manager.connect_printer`) and polls `client.connected`,
  instead of a throwaway test client then a second connection — one LAN connection, which Bambu requires. Unreachable → client
  stays registered (its reconnect produces the first live connection); reached-but-login-failed → `disconnect_printer`.
- A per-printer `asyncio.Lock` serialises auto-test and `POST /printers/{id}/test` for the same printer.
- `PATCH` cannot move a printer to another plugin today (identity edit validates against `printer.plugin_id`), so the spec's
  "different plugin" invalidation case does not arise; not built.
- Discovery rows gain `added_printer: {printer_id, name, configured, config_error} | null` (needed for the "Already in your
  fleet" section's status + Edit/PATCH) besides the spec's `ip_in_use_by`.
- A scan row with no matched model (`model_uuid` null) counts as `already_added` when an existing printer has the same IP and the
  same `plugin_id`; a different plugin on that IP → `ip_in_use_by`.
- Scan rows learn the post-add outcome by polling `GET /printers/{id}` every 2 s for up to 20 s until `configured` or
  `config_error` is set (no new WS subscription in the scan component). Fleet refetches on a new WS event `printer_configured`.
- `PrinterCreate.name` becomes optional; backend fallback name `"<model display_name> <last IP octet>"`.
- Test factory `create_printer` keeps returning a working printer: it posts an `ip_address` and then marks the row configured.
  ORM `configured` default is `False` (safe by default); ORM-seeded test printers that must claim get `configured=True` explicitly.

## Review Focus

1. Re-saving a printer's edit form with untouched connection values (incl. numeric defaults the form fills, e.g. `use_ams: 1`
   vs `"1"`) must not unconfigure it → Task 3 `connection_changed` normalises str/int and blanks; Task 6 test PATCH with
   `{"use_ams": "1"}` against stored `1`.
2. An unconfigured printer that already has a materialized model-target config must still never claim → Task 5 test.
3. Restart with a Bambu printer missing its access code: startup logs one warning, builds no client, server boots → Task 4 test.
4. Batch add where one row 409s/500s: other rows still finish and report → Task 9 test.
5. Fleet backup import of a printer that was configured in the source: it must land unconfigured and get re-tested, not silently
   configured → Task 6 test.

---

### Task 1: Persisted `configured` / `config_error` + serializers

**Files:**
- Create: `backend/app/migrations/v067_printer_configured.py`
- Modify: `backend/app/migrations/runner.py` (import + register), `backend/app/models.py` (`Printer`),
  `backend/app/api/routes/printers.py` (`_to_dict`), `backend/app/api/routes/fleet.py` (`_fleet_dict`),
  `contracts/response-keys.json` (`printer`, `fleet_printer` += `configured`, `config_error`)
- Test: `backend/tests/test_migrations.py`, `backend/tests/api/test_response_contracts.py` (existing assertions cover the keys)

**Interfaces:**
- Produces: `Printer.configured: Mapped[bool]` (`default=False`), `Printer.config_error: Mapped[Optional[str]]` (Text, nullable);
  both in printer and fleet dicts.

- [ ] **Step 1: Failing test** `test_v067_backfills_existing_printers_as_configured`: build a DB at v066 with one printer row
  (use the existing migration-test helpers in `test_migrations.py`), run migrations, assert `configured == 1` and
  `config_error IS NULL`; then insert a row with `configured=0`, re-run `up`, assert it is still 0 (back-fill only when the column
  is added).
- [ ] **Step 2:** `pytest tests/test_migrations.py -k v067 -v` → FAIL.
- [ ] **Step 3:** Migration: `version = 67`, `name = "printer_configured"`; `up` adds `configured BOOLEAN NOT NULL DEFAULT 0` and
  `config_error TEXT` only if absent (PRAGMA check like v066), and in that same branch `UPDATE printers SET configured = 1`;
  `down` drops both. Model columns, serializer keys, contract keys.
- [ ] **Step 4:** `pytest tests/test_migrations.py tests/api/test_response_contracts.py -v` → PASS.
- [ ] **Step 5:** Commit `Add printers.configured and config_error (BIZ-369)`.

### Task 2: Plugin-declared quick-add fields

**Files:**
- Modify: `backend/app/services/abstract_printer_client.py` (`ConnectionField.quick_add: bool = False`),
  `backend/app/plugins/bambu/client.py:264-265` (`quick_add=True` on `serial_number`, `access_code`),
  wherever `/printers/types` serializes connection fields (`printer_identity.printer_model_catalog`) so `quick_add` is emitted,
  `frontend/src/api/printers.ts` (`ConnectionField.quick_add: boolean`), `frontend/src/test/printerTypes.ts` fixtures
- Test: `backend/tests/api/test_printer_types_and_identity.py`, `backend/tests/plugins/` bambu test file that checks its fields

- [ ] **Step 1: Failing tests** `test_types_expose_quick_add`: every `connection_fields` entry of `GET /printers/types` has a bool
  `quick_add`; `test_bambu_quick_add_fields`: `[f.name for f in BambuClient.connection_fields() if f.quick_add] ==
  ["serial_number", "access_code"]`; elegoo/moonraker/mock have none.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS (+ boundary tests).
- [ ] **Step 5:** Commit `Let printer plugins declare quick-add connection fields`.

### Task 3: `printer_setup` pure helpers + shared connection test

**Files:**
- Create: `backend/app/services/printer_setup.py`
- Modify: `backend/app/api/routes/printers.py` (`test_connection` and `_connect_failure_hint` move into the service; the route
  calls it and keeps its response shape `{ok, error?}`)
- Test: `backend/tests/services/test_printer_setup.py`

**Interfaces:**
- Produces:
  - `strip_blank(cfg: dict | None) -> dict` — drops keys whose value is `None` or a whitespace-only string.
  - `missing_required(fields: list[ConnectionField], cfg: dict) -> list[str]` — labels of `required` fields absent after
    `strip_blank`.
  - `missing_reason(labels: list[str]) -> str` — `"Missing Serial Number"`, `"Missing Serial Number, Access Code"`.
  - `connection_changed(old: dict | None, new: dict | None) -> bool` — compares `strip_blank` of both with values normalised via
    `str(v).strip()`.
  - `@dataclass(frozen=True) class TestOutcome: ok: bool; error: str | None; reachable: bool | None`
    (`reachable` None = no endpoint to probe / exception).
  - `async def failure_hint(client) -> tuple[str, bool | None]` — the moved `_connect_failure_hint`, also returning reachability.
  - `async def test_unsaved(printer_type: str, cfg: dict) -> TestOutcome` — the moved route body (throwaway client, poll
    `TEST_CONNECT_POLL_S = 15.0`, always disconnect).

- [ ] **Step 1: Failing tests:** `strip_blank({"a": "", "b": " ", "c": None, "d": 0, "e": "x"}) == {"d": 0, "e": "x"}`;
  `missing_required` on Bambu fields with `{"ip_address": "1.2.3.4"}` → `["Serial Number", "Access Code"]`;
  `connection_changed({"use_ams": 1, "ip_address": "a"}, {"use_ams": "1", "ip_address": "a", "access_code": ""}) is False`;
  `connection_changed({"ip_address": "a"}, {"ip_address": "b"}) is True`; `failure_hint` with a monkeypatched probe returns
  `reachable=True` / `False`. Existing `test-connection` route tests keep passing unchanged.
- [ ] **Step 2–4:** fail → implement → `pytest tests/services/test_printer_setup.py tests/api/test_printers_api.py -v` PASS.
- [ ] **Step 5:** Commit `Extract vendor-neutral printer setup helpers`.

### Task 4: `PrinterManager` configured state + first live connection + startup skip

**Files:**
- Modify: `backend/app/services/printer_manager.py`, `backend/app/main.py:126-127` (load configured set at startup),
  `backend/tests/conftest.py` (`_isolate_printer_manager` also clears `_unconfigured`)
- Test: `backend/tests/services/test_printer_manager.py`, `backend/tests/test_autoconnect.py`

**Interfaces:**
- Produces on `PrinterManager`:
  - `_unconfigured: set[int]`; `set_configured(printer_id: int, configured: bool) -> None`; `is_configured(printer_id) -> bool`.
  - `async load_configured_from_db() -> None` — ids with `configured == False` into `_unconfigured`.
  - `is_printer_ready` returns False when the id is in `_unconfigured`.
  - `async mark_configured(printer_id: int) -> None` — persists `configured=True, config_error=None`, updates the set,
    broadcasts `("printer_configured", {"printer_id", "configured": True, "config_error": None})`.
  - `on_state_change`: after the connectivity edge handling, if `printer_id in _unconfigured` and the client is connected →
    `await mark_configured(printer_id)` (wrapped like the other handlers so telemetry never breaks).
  - `connect_all_enabled_printers`: a printer with `printer_setup.missing_required(fields, cfg)` non-empty gets
    `config_error = missing_reason(...)`, `configured` left as is, one `logger.warning`, no `create_client` call. Fields come from
    `client_class(printer.plugin_id)` via the factory's existing per-model field lookup (`connection_fields_for(model)` when
    present, else `connection_fields()`); expose that as `printer_setup.fields_for(printer) -> list[ConnectionField]`.

- [ ] **Step 1: Failing tests:** `test_unconfigured_printer_is_never_ready` (connected idle fake client, id in set → False;
  `set_configured(id, True)` → True); `test_first_online_state_marks_configured` (seed DB row `configured=False`, register a
  fake client with `connected=False`, flip to True, `await on_state_change(id, state)`, re-read row → `configured` True,
  `config_error` None, broadcast called with `printer_configured`); `test_startup_skips_printer_missing_required_field`
  (Bambu row with only `ip_address`: `create_client` not called — patch it — row's `config_error == "Missing Serial Number,
  Access Code"`, exactly one warning in `caplog`).
- [ ] **Step 2–4:** fail → implement → PASS.
- [ ] **Step 5:** Commit `Track printer configuration in PrinterManager`.

### Task 5: Queue never claims for an unconfigured printer

**Files:**
- Modify: `backend/app/services/queue_engine.py:951` (`or not printer.configured`), `backend/app/services/model_targets.py`
  (`materialize_job` and `sync_targets_for_printer` skip `configured == False` printers)
- Modify tests that seed ORM `Printer(...)` rows expected to claim: add `configured=True` (find them by running the suite)
- Test: `backend/tests/services/test_queue_engine.py`, `backend/tests/services/test_model_targets.py`

- [ ] **Step 1: Failing tests** (follow the existing fake-client claim pattern in `test_queue_engine.py`):
  `test_unconfigured_printer_never_claims` — queued job with an explicit config for the printer, row `configured=False`, manager
  set says ready-capable client: run one `_process_queue`; job still `queued`, `assigned_printer_id` None, no slice started;
  also with `slice_only=True` path. Same for a model-target job (`test_model_target_skips_unconfigured_printer`: no
  `JobPrinterConfig` materialized for it). Flip `configured=True` + `set_configured` → next cycle claims.
- [ ] **Step 2–4:** fail → implement → full `pytest -q` PASS after adding `configured=True` where seeded printers must claim.
- [ ] **Step 5:** Commit `Keep unconfigured printers out of the queue`.

### Task 6: Auto-test service + relaxed create, PATCH invalidation, `POST /{id}/test`, import

**Files:**
- Modify: `backend/app/services/printer_setup.py`, `backend/app/api/routes/printers.py` (`PrinterCreate`, `create_printer`,
  `update_printer`, new `test_saved_printer`), `backend/app/api/routes/settings.py:407-432` (fleet import),
  `backend/tests/conftest.py` (`create_printer` factory; autouse `_no_auto_test` fixture), API tests that post printers with
  `connection_config: {}`
- Test: `backend/tests/api/test_printers_quick_add.py` (new), `backend/tests/services/test_printer_setup.py`,
  `backend/tests/api/test_fleet_import.py`

**Interfaces:**
- Produces in `printer_setup`:
  - `async def evaluate(printer_id: int) -> TestOutcome` — under the printer's lock: load row; missing fields → persist
    `configured=False, config_error=missing_reason`, `printer_manager.set_configured(id, False)`, `disconnect_printer`, return
    `TestOutcome(False, reason, None)`; else `disconnect_printer`, `create_client(printer)`, `connect_printer`, poll
    `client.connected` up to `TEST_CONNECT_POLL_S`; ok → `printer_manager.mark_configured`; failure → `failure_hint`, persist
    `config_error`, and if `reachable is True` → `disconnect_printer` (login failed: no retry storm); broadcast
    `printer_configured` with the outcome. Exceptions become `TestOutcome(False, str(exc), None)`, never raised.
  - `def schedule(printer_id: int) -> None` — `asyncio.create_task` of `evaluate` behind `asyncio.Semaphore(4)`; tasks kept in a
    module set until done. Uses `printer_manager._session_factory` for DB access (same injectable factory as the manager).
  - `def default_name(model_display_name: str, ip: str) -> str` — `f"{model_display_name} {ip.rsplit('.', 1)[-1]}"`.
- Routes:
  - `PrinterCreate.name: str | None = None`. `create_printer`: `cfg = strip_blank(body.connection_config)`; 422
    `"connection_config.ip_address is required"` when absent; 409 `"<name> already uses <ip> as <model>"` when an existing printer
    has the same `ip_address` and identity (Global Constraints); stores `configured=False`; replaces the inline
    `create_client`/`connect_printer` with `printer_manager.set_configured(id, False)` + `printer_setup.schedule(id)`; response
    unchanged otherwise (`configured: false`).
  - `update_printer`: when `body.connection_config is not None` and `connection_changed(stored, new)` → store stripped config,
    `configured=False`, `config_error=None`, after commit `set_configured(id, False)` + `schedule(id)`. Otherwise configured
    untouched (identical values are not re-stored as changed).
  - `POST /{printer_id}/test` (`printers:control`, 404 when missing) → `{"ok", "error", "configured", "config_error"}` from
    `await evaluate(id)` then re-read row. Declared before any conflicting `/{printer_id}` literal route issue (it is a sub-path, no
    clash).
  - Fleet import: imported rows `configured=False`, `schedule(id)` after commit instead of the inline connect.
- conftest: autouse `_no_auto_test` monkeypatches `printer_setup.schedule` with a recorder list (fixture `scheduled_tests`);
  tests marked `@pytest.mark.auto_test` keep the real one. `create_printer` factory defaults
  `connection_config={"ip_address": "192.0.2.<n>"}` (unique per call) and then sets the row `configured=True` +
  `printer_manager.set_configured(id, True)` unless `configured=False` is passed.

- [ ] **Step 1: Failing tests** in `test_printers_quick_add.py`:
  - `test_create_with_only_ip_and_model` — Elegoo model, `{"ip_address": "10.0.0.5", "port": ""}` → 201, response
    `configured is False`, stored `connection_config == {"ip_address": "10.0.0.5"}`, `scheduled_tests == [id]`.
  - `test_create_without_ip_is_422`; `test_duplicate_ip_and_model_is_409`; `test_same_ip_other_model_is_allowed`.
  - `test_default_name_from_model_and_octet` — no `name`, P1S at `192.168.7.30` → `"P1S 30"`.
  - `test_patch_connection_change_unconfigures_and_retests`; `test_patch_name_only_keeps_configured`;
    `test_patch_same_model_keeps_configured`; `test_patch_identical_values_keeps_configured` (stored `use_ams: 1`, sent `"1"`).
  - `test_saved_printer_test_endpoint` — fake client factory: connected → `{"ok": True, "configured": True}` and row configured.
- Failing tests in `test_printer_setup.py` (real `evaluate`, fake client via patched `create_client`):
  - `test_missing_required_field_builds_no_client` — Bambu with only IP: `create_client` never called, `config_error ==
    "Missing Serial Number, Access Code"`, no client registered.
  - `test_auto_test_success_configures`; `test_login_failure_tears_client_down` (`failure_hint` → reachable True: client removed
    from `printer_manager._clients`, `config_error` set); `test_unreachable_keeps_client_registered`.
  - `test_auto_tests_run_in_parallel_capped_at_4` — 6 schedules with a blocking fake; at most 4 in flight.
- `test_fleet_import.py::test_imported_printer_lands_unconfigured_and_is_retested`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement; fix API tests that post `connection_config: {}` by adding an IP (or using
  the factory). **Step 4:** full `pytest -v` + `ruff check .` PASS.
- [ ] **Step 5:** Commit `Relax printer create and auto-test new printers`.

### Task 7: Discovery matches on (IP, model) and flags IP conflicts

**Files:**
- Modify: `backend/app/api/routes/printers.py` (`discover_printers` result building), `contracts/response-keys.json` (new group
  `discovered_printer` incl. `already_added`, `ip_in_use_by`, `added_printer`), `backend/tests/api/test_response_contracts.py`
- Test: existing discovery API test file (`grep -l discover backend/tests/api`)

**Interfaces:**
- Produces per found row: `already_added: bool`, `added_printer: {printer_id, name, configured, config_error} | None`,
  `ip_in_use_by: {printer_id, name, model} | None` (`model` = the existing printer's `model_label`/model display name).

- [ ] **Step 1: Failing tests:** `test_same_ip_same_model_is_already_added` (with `added_printer` filled);
  `test_same_ip_different_model_sets_ip_in_use_by` (`already_added is False`); `test_unmatched_model_same_plugin_is_already_added`;
  contract test for `discovered_printer`.
- [ ] **Step 2–4:** fail → implement → PASS.
- [ ] **Step 5:** Commit `Match discovered printers on IP and model`.

### Task 8: Frontend API + quick-add helpers

**Files:**
- Modify: `frontend/src/api/printers.ts` (`ApiPrinter.configured`, `config_error`; `DiscoveredPrinter.added_printer`,
  `ip_in_use_by`; `CreatePrinterBody.name?`; `testSavedPrinter(id)`), `frontend/src/api/fleet.ts` (`configured`,
  `config_error` → `Printer.configured`, `configError`)
- Create: `frontend/src/components/quickAdd.ts`
- Test: `frontend/src/components/quickAdd.test.ts`, `frontend/src/api/fleet.test.ts`

**Interfaces:**
- Produces in `quickAdd.ts`:
  - `quickAddFields(t: PrinterType): ConnectionField[]` — `connection_fields.filter(f => f.quick_add)`.
  - `stripBlank(cfg: Record<string, unknown>): Record<string, unknown>`.
  - `missingQuickAdd(t: PrinterType, values: Record<string, string>): string[]` — labels of required quick-add fields left blank.
  - `defaultNickname(p: DiscoveredPrinter, t: PrinterType | null): string` — `p.name` else `` `${t.display_name} ${lastOctet}` ``.
  - `type RowStatus = 'ready' | 'missing' | 'testing' | 'test_ok' | 'test_failed' | 'adding' | 'added_configured' |
    'added_unconfigured' | 'exists'`; `statusLabel(s: RowStatus, detail?: string): string` with the spec's chip copy
    ("Ready to add", "Missing <field>", "Testing…", "Connection OK", "Test failed", "Adding…", "Added · configured",
    "Added · not configured: <reason>", "Already added").
  - `runPool<T, R>(items: T[], limit: number, fn: (t: T) => Promise<R>): Promise<PromiseSettledResult<R>[]>`.

- [ ] **Step 1: Failing tests** for each helper (incl. `runPool` never exceeding the limit and settling a rejected item without
  stopping the others); fleet mapping of `configured`/`config_error`.
- [ ] **Step 2–4:** `npx vitest run src/components/quickAdd.test.ts src/api/fleet.test.ts` fail → implement → PASS.
- [ ] **Step 5:** Commit `Add quick-add helpers and API fields`.

### Task 9: In-place multi-row `DiscoverPrinters`

**Files:**
- Modify: `frontend/src/components/DiscoverPrinters.tsx` (rewrite), `frontend/src/components/PrinterPluginDiscovery.tsx`,
  `frontend/src/screens/PrintersScreen.tsx:459` (step-1 scan), `frontend/e2e/mock-api.ts` (+ any discovery e2e spec)
- Test: `frontend/src/components/DiscoverPrinters.test.tsx`, `frontend/src/components/PluginSettingsPage.test.tsx`,
  `frontend/src/screens/PrinterAddForm.discovery.test.tsx`

**Interfaces:**
- Consumes: Task 8 helpers; `fetchPrinterTypes`, `createPrinter`, `updatePrinter`, `testConnection`, `fetchPrinter`.
- Props become `{ pluginId?: string; onMoreSettings?: (p: DiscoveredPrinter) => void; onAdded?: () => void }` (`onPick` removed).
  `PrintersScreen` passes `onMoreSettings` (opens the full form pre-filled, as `onPick` did) and `onAdded` (refresh list);
  `PrinterPluginDiscovery` keeps its full-form fallback via `onMoreSettings`.

Behaviour (per spec "Scan-results UI"; mockup only if unclear):
- Fetch types once with the scan; per row the model options are enabled types with the row's `plugin_id`, in a type-ahead
  `<input list>` / filtered listbox labelled `Model for <ip>`; pre-selected from `model_uuid`, required otherwise.
- New rows grouped by plugin (`display_name` heading). Row: checkbox `Select <ip>` · nickname input (`defaultNickname`) · IP ·
  quick-add inputs (or "Nothing to enter"; values from `connection_config` tagged "from scan") · Test (only when the plugin has
  quick-add fields; enabled when all have values; editing clears the result) · chip · Add · "More settings…".
- `ip_in_use_by` rows: warning line `"<ip> is already used by <name> (<model>) — the address may have changed"`, start unchecked,
  "Edit <name>" button that opens that printer's quick-add fields inline (same as the already-added Edit below, PATCHing the
  existing printer — the Printers screen has no deep-link to its edit form); excluded from "All new".
- "Already in your fleet" section: read-only rows with `added_printer.name` + configured chip; Edit reveals quick-add fields +
  Test; Save → `updatePrinter(id, {connection_config})` with merged stripped values.
- Footer: "N selected", warning when selected rows miss quick-add fields, selectors All new / Complete only / None, primary
  `Add N printers` → `runPool(selected, 4, createRow)`. Each row: `createPrinter` with `stripBlank` config → chip "Adding…" →
  poll `fetchPrinter` (2 s, 20 s cap) → added_configured / added_unconfigured(`config_error`); 409 → "Already added"; other
  error → chip shows message, row stays checkable. Unconfigured added rows keep inputs + Save (PATCH, then poll again).
- Results persist until the next Scan; nothing navigates away.

- [ ] **Step 1: Failing tests** (`stubFetch`):
  `renders quick-add inputs only from quick_add metadata` (Bambu row has Serial/Access code inputs, Elegoo row says
  "Nothing to enter"); `batch add keeps results and reports per row` (3 rows, second POST 409, third 500: rows show
  "Added · configured", "Already added", error; table still rendered; at most 4 concurrent POSTs);
  `ip conflict row starts unchecked and All new skips it`; `already-added row edits via PATCH`;
  `model is required when the scan did not match one` (Add disabled until chosen); `test button enabled only when quick-add
  fields are filled and editing clears the result`; existing plugin-settings discovery test adjusted to the new flow.
- [ ] **Step 2–4:** fail → implement → `npx vitest run` PASS; `npm run build`, `npm run lint`, `npm run test:e2e` PASS.
- [ ] **Step 5:** Commit `Add printers from scan results in place`.

### Task 10: Fleet shows "Setup incomplete" distinctly from "Queue off"

**Files:**
- Modify: `frontend/src/screens/FleetScreen.tsx` (`SetupIncompleteBadge` next to `QueueOffBadge` at both sites ~962/1049,
  `cardCueStyle` warn cue, card section with quick-add inputs + Test / Save & connect), the fleet live-update hook that handles
  WS events (refetch on `printer_configured`), `backend/app/api/routes/printers.py` is already done (Task 6)
- Test: `frontend/src/screens/FleetScreen.test.tsx`

- [ ] **Step 1: Failing tests:** `queue off and setup incomplete are distinct` (printer A `queue_on=false, configured=true` → only
  "QUEUE OFF"; printer B `configured=false, config_error="Missing Access Code"` → "SETUP INCOMPLETE" + the error text; printer C
  both → setup badge first and queue toggle still present); `setup card Test calls POST /printers/{id}/test and refetches`;
  `Save & connect PATCHes connection_config`.
- [ ] **Step 2–4:** fail → implement (badge `--warn` colour) → `npx vitest run`, `npm run build` PASS.
- [ ] **Step 5:** Commit `Show incomplete printer setup in Fleet`.

### Task 11: Contracts, docs, full verification, review, PR

**Files:**
- Modify: `openapi.json` (`python scripts/export_openapi.py` from repo root), `docs/agent/backend.md`, `frontend.md`,
  `data-model.md`, `printers.md`, `conventions.md` (eligibility invariant now includes `configured`), `docs/user-guide.md`
  (adding printers from a scan) — via `themis-docs-sync`.

- [ ] **Step 1:** Regenerate `openapi.json`; run backend `pytest -v -ra --cov`, `ruff check .`; frontend `npm run build`,
  `npm run lint`, `npm run test:cov`, `npm run test:e2e`. All green; raise coverage floors if they grew.
- [ ] **Step 2:** Docs sync; commit `Document discovery quick-add (BIZ-369)`.
- [ ] **Step 3:** One fresh reviewer subagent (base `develop` SHA, head SHA, this plan, `docs/agent/backend-review.md` +
  `frontend-review.md`); address Critical/Important; re-run suites; write `.claude/review-state.json`.
- [ ] **Step 4:** Push, open PR into `develop`, enable auto-merge, Linear → Confirmation testing with the PR link.
