# BIZ-251 Printer vendors as plugins — implementation plan

Companion to `2026-10-09-biz-251-printer-plugins-handoff.md` (decisions live there and in BIZ-251). This file is the file-level scope. Not yet implemented.

## 0. Doc drift found (sync with `themis-docs-sync` after merge)
- `docs/agent/printers.md`, `backend.md`: `on_ams_change` said to be wired in `main.py:125`. Actual wiring: `printer_manager.connect_printer` (`printer_manager.py:262-265`). `main.py` only references the name.
- `docs/agent/printers.md` / `recipes.md`: remap is called as `client.remap_sliceable_3mf` from `queue_engine`. This plan moves it to `SlicingProvider.apply_tool_mapping`.
- `docs/agent/backend.md`: says `printer_manager` owns its own `SessionLocal`. Keep as-is; not touched here.

## 1. Change shape
Recipe: **Add a printer vendor** (×4 vendors, extracted) + new **routed capability mode** (`plugins/`) + **Add a table/column** (`printers` identity columns) + **Wire a live event** (bus) + **Change queue/print behavior** (claim gate via plugin state) + **Add an API route** (plugin-type discovery). Cross-cutting.

## 2. Phases (each green before next; one PR, commits per phase)

### Phase A — Routed capability + typed contract (BIZ-250 slice)
- `backend/app/plugins/capabilities/` — add `printer_client` capability definition (`mode="routed"`). Extend `CapabilityDef` with `mode: Literal["exclusive","routed"]`; default `exclusive`. Find `CapabilityDef` in `capabilities/__init__.py` (or `filament_inventory.py` pattern).
- `backend/app/plugins/host.py`:
  - `active(cap)` unchanged for exclusive. Add `active_for(cap, key)` (routed: plugin_id from `key`, enabled + instance built) and `call_for(cap, binding, fn, *, timeout) -> CallResult[R]` where `fn: Callable[[Protocol], Awaitable[R]]`.
  - `set_provider` for routed caps: no single selection; "enable plugin" = provides all its routed caps. Adjust `_selections` semantics: routed caps store no selection row (or a sentinel).
  - `status()` for routed: `serving` per plugin, not per cap.
- `backend/app/plugins/manifest.py` — `PluginManifest.provides` validated against Protocol at load (`isinstance` against `runtime_checkable` Protocol + signature check via `inspect.signature`). Reject with `build_error`.
- New `backend/app/plugins/printer.py` (or under `capabilities/printer_client.py`): `PrinterProvider(Protocol)` with the methods currently on `AbstractPrinterClient` that core calls; pydantic DTOs `PrinterState`, `Alarm`, `DiscoveredPrinter`, `PrinterFile`, `StartPrintOptions`, `ConnectionField`, `PrinterCapabilities`, `Trays`/`LoadedFilament`. Move from `services/abstract_printer_client.py` (keep re-exports during migration).
- Test: `backend/tests/plugins/test_routed_capability.py` — two fake routed providers; each binding reaches only its provider; Protocol-nonconforming plugin rejected at load; inventory exclusive tests still green (`tests/test_provider_boundary.py`).

### Phase B — Minimal event bus (BIZ-249 slice)
- New `backend/app/services/events.py`: `EventBus` (in-process, asyncio). `publish(event: Event)` (typed pydantic base `Event`, `name: str`), `subscribe(event_type, handler)`. Handlers run in `asyncio.create_task`, failures logged (not raised). No persistence, no envelope version.
- Event types (new, `backend/app/services/printer_events.py`): `PrintCompleted`, `PrinterStateChanged`, `AmsChanged`, `AlarmRaised`/`AlarmsChanged`.
- `printer_manager.py` lines 181-265: replace the three callback methods' bodies with `bus.publish(...)`; subscribers registered in `printer_manager.__init__` / lifespan: state/complete/AMS handlers call the existing logic. Remove `_on_*` attrs from clients (`abstract_printer_client.py` callback attrs); clients publish via a `PrinterEvents` sink passed by the manager (not via `main.py`).
- Test: `tests/services/test_events.py` (handler failure isolation, publish returns before handler completes); `tests/services/test_printer_manager.py` existing cases still pass with bus.

### Phase C — Printer model identity (BIZ-262 slice, row migration)
- `backend/app/models.py` `Printer`: add `plugin_id: str`, `manufacturer_id: str`, `model_id: str` (keep `printer_type` for one release, read-only). Declare in model (test fixtures use `create_all`).
- Migration `backend/app/migrations/v042_printer_model_identity.py`: `ALTER TABLE printers ADD COLUMN` guarded (`PRAGMA table_info`); backfill via mapping:
  - `bambu` → (`bambu`, `bambu`, `p1s`)
  - `elegoo_centauri` → (`elegoo_centauri`, `elegoo`, `centauri`)
  - `snapmaker_extended` → (`snapmaker`, `snapmaker`, `u1_extended`)
  - `mock` → (`mock`, `mock`, `mock`)
  Register in `migrations/runner.py` (`_MIGRATIONS`). Confirm version number is next free (last is v041).
- `backend/app/services/printer_client_factory.py` — `REGISTRY` replaced by plugin lookup: `get_printer_types_for_ui()` now builds from plugin manifests (`printer` contributions). `create_client(printer)` resolves `plugin_id` → instance via `host.part_for(...)`. Keep `printer_type` fallback during migration.
- Manifest contributions (in each plugin's `manifest`): `manufacturers[] -> models[]` with `id`, `name`, `bed_mm`, `toolheads`, `connection_fields`. Bambu lists all models (P1S, P1P, X1C, X1E, A1, A1 mini, H2D, …). Elegoo: Centauri. Snapmaker: U1 (Extended). Mock: mock. Plus a `custom` entry only if a plugin declares one (none for these four).
- `backend/app/api/routes/printers.py` `GET /types` — response shape keys: `plugin_id`, `manufacturer_id`, `model_id`, `display_name`, `connection_fields`, `bed_mm`. Update `contracts/response-keys.json` and regenerate `openapi.json` (`python scripts/export_openapi.py`).
- `PrinterCreate`/`PrinterPatch` in same route: accept `plugin_id`, `manufacturer_id`, `model_id`; reject unknown; keep `printer_type` accepted on input with mapping for old clients.
- Dormant: `services/printer_manager.py` — printers whose plugin is disabled excluded from polling + queue (`is_printer_ready` returns False; `status` = dormant); removed plugin → `printers.plugin_id` orphan flagged in `fleet.py` output.
- Test: `tests/test_printer_model_migration.py` (each legacy type → expected triple; connection settings preserved); `tests/api/test_printer_types.py`.

### Phase D — Vendor plugins
For each vendor (Bambu, Elegoo, Snapmaker, Mock):
- Create `backend/app/plugins/<vendor>/` with `__init__.py` (manifest), `client.py` (moved from `services/<vendor>_client.py`; keep old module as import shim for one release if tests import it), `alarms.py` (alarm code tables moved from `services/alarm_codes.py` / `services/alarms.py` — plugin publishes neutral `AlarmRaised` events), `discovery.py` (`discover_host`, SSDP matchers moved from `services/discovery.py` / `bambu_mqtt.SSDP_PORTS`), `camera.py` (feed code: `camera_proxy` RTSP/ffmpeg, snapshot/stream — behind camera contract).
- Plugin manifest: `themis-plugin.toml` (bundled, per `plugins/package.py`) + `MANIFEST` in `__init__.py`. Mock manifest sets `default_enabled=False` for production (dev/test enable via settings or `THEMIS_ENV`).
- Snapmaker plugin: `remap` NOT imported (see Phase E).
- Existing vendor tests move with the clients: `tests/services/test_bambu_mqtt.py`, `test_elegoo_centauri_client.py`, `test_snapmaker_client.py` — update imports only (behavior unchanged).
- Virtual printers: `tests/virtual_printers/` — ensure fakes load through plugin path; `tests/test_verification_suite_against_virtual_printers.py` green.
- Register `printer_client` in each manifest's `provides`.

### Phase E — Remap moves behind SlicingProvider (interim, code stays in Themis)
- `backend/app/services/providers/slicing.py` — add `apply_tool_mapping(self, source_3mf: Path, *, tool_index: int | None, filament_map: list[dict] | None) -> None` to `SlicingProvider` ABC, default raises `NotImplementedError`; flag `TOOL_MAPPING: ClassVar[bool]`.
- `backend/app/services/providers/laminus/` — implement `apply_tool_mapping` by delegating to `services/snapmaker/remap.remap_3mf` (code unchanged, still in `services/snapmaker/`). Set `TOOL_MAPPING=True`.
- `backend/app/services/queue_engine.py` `_run_slice_and_print`: replace `prepare_hook = lambda p: client.remap_sliceable_3mf(...)` with a `prepare_hook` built from `get_slicing_provider().apply_tool_mapping` (the slicer owns mapping).
- `backend/app/services/abstract_printer_client.py` — remove `remap_sliceable_3mf`. `snapmaker_client.py:566` `from .snapmaker.remap import remap_3mf` (local lazy import) — remove.
- `tests/test_provider_boundary.py`: add rule — `plugins/snapmaker/` may not import `services.snapmaker` nor `providers.laminus`.
- Tests: `tests/services/test_snapmaker_remap.py` (existing) stays green; new `tests/services/test_apply_tool_mapping.py` (provider delegates; no-op when neither set).

### Phase F — Core cleanup (routes, camera, discovery)
- `backend/app/api/routes/printers.py` — remove vendor branches: `_require_capability` stays, but capability flags from plugin `get_capabilities()`. Remove ffmpeg check (~L1190) → plugin reports `camera` capability.
- `backend/app/api/routes/cameras.py` — keep generic routes; vendor URL/auth moved to plugin camera contract (`snapshot()`, `open_stream()` → JPEG frames). `services/camera_hub.py` stays core, consumes contract.
- `services/discovery.py` + `discovery_net.py` — iterate `host.routed_plugins("printer_client")` for `discover_host`; SSDP matchers come from plugin manifests.
- `services/printer_manager.py` `_STATUS_SERIALIZERS` — per-plugin serializer (lives in plugin); keys unchanged (normalized dict contract in `printers.md`).
- `tests/test_no_provider_in_core.py` — extend ratchet: core must not name `bambu|elegoo|snapmaker|mock` modules (allowlist shrinks to zero; migration shims allowed until removed).
- `tests/test_provider_boundary.py` — core imports no `plugins/<vendor>` module.

### Phase G — Frontend
- `frontend/src/api/printers.ts` — `PrinterType` gains `plugin_id`, `manufacturer_id`, `model_id`, `bed_mm`; `Printer` gains same. Type strict; `npm run build`.
- Add-printer flow (`screens/FleetScreen.tsx` / printer editor): two dropdowns (manufacturer → model) fed from `GET /printers/types`; connection fields from selected entry. Mock hidden when `enabled=false`. Keep layout (no redesign).
- `contracts/response-keys.json` — add the new keys; `src/api/responseKeys.contract.test.ts` green.
- `styling.md`: if a dormant/orphan status is shown, add the key to `StatusKey` + `ui.tsx` tone map (recipe "Style a component").
- Tests: Vitest for dropdown filtering and mock-hidden case.

## 3. Invariants to respect
- `awaiting_plate_clear` lives in DB + `PrinterManager` set; keep in sync through the bus migration (Phase B).
- Blocked vs failed: a dormant printer must **block** (not fail) jobs.
- Head-of-line queue: no change; a dormant printer makes its job wait, does not skip.
- Per-printer flags read from `self._*` in `start_print`; `StartPrintOptions` unchanged.
- Auth: every new route has `require_scope`; new plugin routes via `mount_plugin` only.
- Migrations: new column on `printers` = model field + idempotent guard in migration.
- Filament ask vs profile: not touched.
- Cancel ↔ stop: vendor `stop_print` path must stay wired through the routed call.

## 4. Verification gates (per phase, and before PR)
- `cd backend && pytest -v` (coverage floor in `pyproject.toml`: raise, never lower).
- `cd frontend && npm run build && npx vitest run`.
- `python scripts/export_openapi.py` (CI diffs `openapi.json`).
- `contracts/response-keys.json` checks both suites.
- Manual: `backend/protocol_verification/` (read-only default) is not run in CI; virtual-printer suite is.

## 5. Open items (not blocking)
- Snapmaker remap move to Laminus multi-material (BIZ-248) — post-PR.
- BIZ-262 UUIDs/enabled subsets — post-PR.
- Exact `printer_type` removal — after one release.
- Bambu model list source (hardcoded in manifest vs data file): default hardcoded; revisit with BIZ-262.
