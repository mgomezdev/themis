# Provider Interfaces (Laminus & Spoolman)

Themis core never talks to Laminus (slicing) or Spoolman (filament inventory) directly. Every call goes
through an interface in `backend/app/services/providers/`, using the same pattern as
`AbstractPrinterClient` (see `printer-interface.md`): an ABC, capability flags, and a registry + accessor.

```
core (queue_engine, routes, services)
        │  neutral DTOs + ABC methods only
        ▼
providers/slicing.py              providers/filament_inventory.py
  SlicingProvider (sync)            FilamentInventoryProvider (async)
  Catalog / Preset / SliceSpec      Filament / Spool
        │ registry                          │ registry
        ▼                                   ▼
providers/laminus/                providers/spoolman/
  adapter.py  LaminusSlicingProvider  adapter.py  SpoolmanInventoryProvider
  sidecar_client.py  (httpx)          service.py  (httpx)
  gcode.py overrides.py profile_index.py preset_resolver.py
```

**Scope.** This is the interface layer for *current behavior*: no plugin host, no API change. Routes keep
their `/api/v1/laminus/*` and `/api/v1/spoolman/*` paths and response shapes (adapters re-serialize via the
DTOs' `raw` field), so `openapi.json`, `contracts/response-keys.json` and the frontend are untouched.
Persisted ids (`loaded_filaments[].spoolman_spool_id`, `filament_id` columns, low-stock overrides, preset
names, `machine_uuid`/`process_uuid`) stay opaque provider refs and are not renamed.

## The boundary (enforced)

`backend/tests/test_provider_boundary.py` walks every module under `app/` and fails if code **outside**
`app/services/providers/<adapter>/` imports an adapter-internal module (the sidecar client, the Spoolman
client, the Orca override/gcode/preset logic, or their old `app/services/*` paths), names a vendor symbol
(`LaminusSidecarClient`, `SidecarError`, `parse_gcode_estimates`, `get_laminus_sidecar_url`), or imports
`httpx` without being on the `HTTPX_ALLOWED` list (printers, webhooks, notifications, camera, discovery).
Only the two accessor modules (`providers/slicing.py`, `providers/filament_inventory.py`) may import an
adapter package, to register it. The test is in CI (`pytest`) and has planted-violation cases proving it
can fail. Importing the DTOs / ABCs from `providers.slicing` / `providers.filament_inventory` is always fine.

## `FilamentInventoryProvider` — async

`providers/filament_inventory.py`. Mirrors what the Spoolman client does today.

| Method | Returns | Notes |
|---|---|---|
| `test_connection()` | `dict` | Spoolman: `GET /api/v1/info`. |
| `list_filaments()` / `get_filament(ref)` | `list[Filament]` / `Filament` | |
| `list_spools()` | `list[Spool]` | Routes batch: **one call per request** (queue/jobs preflight). |
| `record_usage(spool_ref, grams)` | `None` | Fire-and-forget at the call site (`queue_engine._deduct_spool`): errors are logged, never raised. |
| `get_profile_bindings(filament_ref)` / `set_profile_bindings(filament_ref, bindings)` | `dict[str, list[str]]` / `Filament` | `{printer_preset: [filament profile names]}` — see below. |

Capability flags (class attrs, default `False`): `TRACKS_WEIGHT`, `RECORDS_USAGE` (gates the completion
deduction), `PROFILE_BINDINGS` (gates the drift check, remap writes and `PATCH /spoolman/filaments/{id}`;
a provider without it is skipped, never errors).

DTOs: `Filament(ref, name, vendor, material, color_hex, profile_bindings, raw)`,
`Spool(ref, filament_ref, filament_name, filament_vendor, filament_material, location, remaining_weight,
archived, raw)`. `ref`s are strings. `raw` is the vendor payload, kept so legacy routes return the exact
shape they always did (`[f.raw for f in …]`).

Errors: `InventoryProviderError(message, code, status)` — `code` is the HTTP status string or the transport
exception class name (stored in `spoolman_config.last_sync_error_code`); `status` the upstream HTTP status
when there was one (the PATCH route passes it through, else 503).

Accessors: `get_inventory_provider(session)` → provider or `None` when `SpoolmanConfig` is missing, disabled
or has no URL (replaces every per-caller `enabled`/`url` check); `make_inventory_provider(url, api_key)` for
explicit credentials (the "test connection" button, before anything is saved).

**Profile bindings** are the one place Laminus and Spoolman meet: Spoolman filaments store Orca preset names
in `extra.orca_profiles` (double-JSON-encoded). Only the Spoolman adapter encodes/decodes it; core reads
`Filament.profile_bindings` / `get_profile_bindings` and writes via `set_profile_bindings`. They stay stored
in Spoolman (moving them to a Themis table is out of scope).

## `SlicingProvider` — sync

`providers/slicing.py`. Covers the Laminus sidecar surface plus the slicer-specific file-format knowledge.

| Method | Notes |
|---|---|
| `identity` (property) | Stable id (Laminus: the URL); keys the slice-cache fingerprint memo. |
| `health(timeout=None)` | Raises `SlicingProviderNotReady` (answered, can't slice yet) or `SlicingProviderError`. Queue preflight uses `health(2)`. |
| `get_catalog() -> Catalog` | |
| `merged_config(machine_ref, process_ref, filament_refs, timeout=None)` | Slice-cache fingerprint uses `timeout=10`. |
| `slice(SliceSpec, output_dir) -> artifact path` | Start → poll (≤ ~620 s) → download. `SliceSpec.prepared` = a ready-to-slice project (`PREPARED_PROJECT`). |
| `catalog_health(timeout)` / `request_catalog_rebuild(timeout)` | Catalog readiness (200 → body, 503 → "building" marker) and "rebuild now"; used by `catalog_service` status/rescan. |
| `arrange(path, …)` / `pack_models(paths, machine_ref=…, process_ref=…, filament_refs=…, bed=…)` | `ARRANGE` / `PACK_MODELS`; project generation checks the flag. |
| `parse_estimates(artifact, plate)` → `(grams, seconds, per_extruder)` | Format knowledge — local only. |
| `inspect_overrides(project, merged_config, slots)` / `curated_override_keys()` | Format knowledge — local only. |
| `compatible_presets(catalog, machine_preset, kind)` | Default: presets naming the machine in `compatible_printers`. |

Capability flags: `ARRANGE`, `PACK_MODELS`, `PREPARED_PROJECT`.

DTOs: `Catalog(machines, processes, filaments, raw)` of `Preset(ref, name, compatible_printers, raw)`;
`catalog.ref_for(kind, name)` / `names(kind)` / `refs(kind)` replace the ad-hoc `{m["name"]: m["uuid"]}`
maps. `Catalog.raw` is the legacy `{"machine": […], "process": […], "filament": […]}` JSON the catalog route
serves unchanged. Errors: `SlicingProviderError` (`SidecarError` maps to it); `SlicingProviderNotReady`.

Accessors:
- `get_slicing_provider()` → provider or `None` when no server is configured (`LAMINUS_SIDECAR_URL`). `None`
  blocks queue jobs with the same reason strings as before ("not configured / not ready / unreachable").
- `get_format_provider()` → **never `None`**. The format methods (`parse_estimates`, `inspect_overrides`,
  `curated_override_keys`) are local file work and must not call the server, so gcode in the library still
  parses with no sidecar configured.

### The sync/async rule

The slicing provider is **sync** and is called from the queue's `ThreadPoolExecutor` (4 threads; a slice
polls up to ~620 s) or via `asyncio.to_thread` / `run_in_executor` from routes. Never call its methods on the
event loop directly — the queue loop's non-blocking behavior depends on it. The inventory provider is **async**
(httpx `AsyncClient`) and is awaited directly. Per-call `timeout` overrides exist so existing short timeouts
(health 2 s, fingerprint 10 s, catalog health 5 s) are preserved; defaults (client 630 s, poll 620 s) are
unchanged.

### Catalog service

`services/catalog_service.py` owns the process-wide catalog cache (`get_cached_catalog() -> Catalog`,
`warm()`, `refresh(session)`, `rescan(session)`, `status()`, the 30 s health memo and the pending drift
remap). `routes/laminus.py` is a thin HTTP layer over it; nothing else imports a route module. It raises
`CatalogUnavailable(detail, status)` (503 unconfigured / 502 unreachable / 504 rescan timeout) that routes
map to `HTTPException`.

## Adding a provider

1. Add `providers/<name>/adapter.py` with a class implementing the ABC (set the capability flags), plus any
   vendor client/format modules next to it. Map vendor errors to `InventoryProviderError` /
   `SlicingProviderError`; give the DTOs a `raw` payload if a legacy route re-serializes it.
2. Add `providers/<name>/__init__.py` calling `register_inventory_provider("<name>", Cls)` /
   `register_slicing_provider("<name>", Cls)`.
3. Add one entry to the accessor in `providers/slicing.py` / `filament_inventory.py` (they currently
   instantiate the sole registered adapter; selection by config is the next step and belongs to the plugin
   epic), and add the new package to `ADAPTER_PACKAGES` in `test_provider_boundary.py`.
4. Add a `Fake…Provider` to `backend/tests/fake_providers.py` (if the ABC grew) and run the provider contract
   suite against it (`tests/services/test_inventory_provider_contract.py`,
   `test_slicing_provider_contract.py`): the fake and every adapter are parametrized through the same tests.

## Testing

- `backend/tests/fake_providers.py`: `FakeInventoryProvider`, `FakeSlicingProvider` (records `calls`,
  `fail_with` / `fail_on[method]`, canned `estimates` / `merged` / `health_script`). Patch the accessor **in the
  module under test** (`patch("app.api.routes.jobs.get_inventory_provider", AsyncMock(return_value=fake))`);
  the accessor reads `config.get_laminus_sidecar_url()` at call time, so `patch("app.config.get_laminus_sidecar_url")`
  also still works.
- Adapter tests run against the real wire formats: Spoolman via `spoolman_upstream` (the real
  `tests/spoolman_mock.py` app behind an httpx transport), Laminus via a mock `httpx.Client` transport
  (`sidecar` fixture in `test_slicing_provider_contract.py`).
- `tests/catalog_helpers.py` primes/inspects the catalog cache (`prime_catalog`, `cached_raw`,
  `patch_cached_catalog`).
