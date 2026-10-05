# Provider Interfaces (Laminus & filament inventory)

Themis core never talks to Laminus (slicing) or to an inventory system (Spoolman, Local inventory...) directly.
Slicing goes through `backend/app/services/providers/slicing.py` (an ABC, capability flags, a registry + accessor, the
same pattern as `AbstractPrinterClient`, see `printer-interface.md`). **Filament inventory is a plugin kind** (BIZ-202):
the ABC and DTOs live in `backend/app/plugins/kinds/filament_inventory.py`, every inventory system is a plugin
(`backend/app/plugins/<id>/`), and core reaches the active one only through the plugin host
(`backend/app/services/inventory/`).

```
core (queue_engine, routes, services)
        │  neutral DTOs + ABC methods only
        ▼
providers/slicing.py                     app/services/inventory/*  →  plugins/host.py  (host.call: contained, capability-gated)
  SlicingProvider (sync)                        │ the active filament_inventory plugin
  Catalog / Preset / SliceSpec                  ▼
        │ registry                       plugins/kinds/filament_inventory.py   FilamentInventoryProvider (async)
        ▼                                  InvMaterial / InvSpool, capabilities
providers/laminus/                              │ registry (plugins/__init__.py)
  adapter.py  LaminusSlicingProvider            ▼
  sidecar_client.py  (httpx)             plugins/spoolman/   provider.py  client.py (httpx)  labels.py  settings.py
  gcode.py overrides.py profile_index.py preset_resolver.py
```

**Scope.** Laminus keeps its `/api/v1/laminus/*` paths and response shapes (the adapter re-serializes via the DTOs' `raw`
field). The deprecated `/api/v1/spoolman/*` + `/api/v1/settings/spoolman*` aliases keep their shapes too (byte-for-byte
goldens in `tests/golden/`), now relayed through the inventory provider. Persisted ids (`loaded_filaments[].spoolman_spool_id`,
`filament_id` columns, preset names, `machine_uuid`/`process_uuid`) stay opaque refs for now (BIZ-217 adds provider-namespaced ones).

## The boundary (enforced)

`backend/tests/test_provider_boundary.py` walks every module under `app/` and fails if code **outside**
`app/services/providers/laminus/` or `app/plugins/spoolman/` imports an adapter-internal module (the sidecar client, the Spoolman
client, the Orca override/gcode/preset logic, or their old `app/services/*` paths), names a vendor symbol
(`LaminusSidecarClient`, `SidecarError`, `parse_gcode_estimates`, `get_laminus_sidecar_url`), or imports
`httpx` without being on the `HTTPX_ALLOWED` list (printers, webhooks, notifications, camera, discovery).
Only the two accessor modules (`providers/slicing.py`, `providers/filament_inventory.py`) may import an
adapter package, to register it. The test is in CI (`pytest`) and has planted-violation cases proving it
can fail. Importing the DTOs / ABCs from `providers.slicing` / `providers.filament_inventory` is always fine.

## `FilamentInventoryProvider` — async (a plugin kind)

`plugins/kinds/filament_inventory.py` (spec: Linear "Plugin architecture — design spec" §3.2). Core and the frontend only
see the neutral DTOs and branch on **capabilities**, never on a plugin id.

| Method | Returns | Notes |
|---|---|---|
| `test_connection()` | `dict` | Spoolman: `GET /api/v1/info`. |
| `list_materials()` / `list_spools()` | `list[InvMaterial]` / `list[InvSpool]` | Routes batch: **one call per request** (queue/jobs preflight). |
| `get_spool(ref)` | `InvSpool \| None` | `None` = not found (404). Used by the interim deduction (and, later, the pre-print snapshot). |
| `set_remaining(ref, grams)` | `None` | `WRITE_WEIGHT`. **Absolute**, never a delta (D6): re-sending is harmless. Spoolman: `PATCH /spool/{id} {remaining_weight}` (`protocol_verification/test_spoolman_weight.py`). |
| `set_profile_links(material_ref, links)` | `InvMaterial` | `PROFILE_LINKS_WRITE`. `{orca printer preset: [orca filament presets]}`. |
| `create_material(MaterialDraft)` / `update_material(ref, patch)` / `archive_material(ref, archived=True)` | `InvMaterial` | `MANAGE_MATERIALS`. Patch keys ⊆ `MATERIAL_FIELDS` (name, material, color_hex, vendor, density, diameter). Archive is reversible; `list_materials` still returns archived ones (`archived=True`), core hides them unless `include_archived`. |
| `create_spool(SpoolDraft)` / `update_spool(ref, patch)` / `archive_spool(ref, archived=True)` | `InvSpool` | `MANAGE_SPOOLS`. Patch keys ⊆ `SPOOL_FIELDS` (label, location); weight has its own absolute `set_remaining`. `remaining_g` defaults to `initial_g`. Errors: `InventoryProviderError` status 404 (unknown ref) / 422 (invalid). |
| `parse_label(text)` / `spool_url(ref)` | `ref \| None` / `url \| None` | `LABEL_SCAN` / optional deep link. |

Capabilities (`frozenset` class attr; the manifest declares the same set): `TRACKS_WEIGHT`, `WRITE_WEIGHT`,
`PROFILE_LINKS_READ`, `PROFILE_LINKS_WRITE`, `LABEL_SCAN`, `REMOTE` (can be unreachable → sync loop, health, later the offline cache), `MANAGE_MATERIALS`, `MANAGE_SPOOLS` (the provider owns its library; a provider without them is still valid — its library is managed in the external system).
Optional methods raise `NotSupported(capability)` by default; callers check `provider.has(cap)` first.

DTOs: `InvMaterial(ref, name, material, color_hex "#RRGGBB", vendor, density, diameter, profile_links, raw)`,
`InvSpool(ref, material_ref, material, remaining_g, location, label, archived, raw)`. Refs are strings. `raw` is
provider-private (the plugin's own alias routes relay it); core code never reads it and the neutral API never serialises it.
Errors: `InventoryProviderError(message, code, status)` — `code` is the HTTP status string or the transport exception class name.

**Core access** is `app/services/inventory/`: `provider.py` (`active_provider()`, `has(cap)`, `require(cap)` → raises
`CapabilityUnavailable` → HTTP 409 `{"error": "capability_unavailable", "kind", "capability"}`, `call(method, …)` → the host's
contained `CallResult`), `read.py`, `alerts.py` (`spool.low`), `preflight.py` (`low_stock_warning`), `sync.py` (generic sync
loop + health in `plugin_configs.state`), `deduction.py` (interim read→set; BIZ-218 replaces it), `config.py`
(`inventory_config`: `deduct_on_complete` + low-stock thresholds, keys namespaced `"<provider>:<ref>"`).

**Profile links** are the one place Laminus and the inventory meet: Spoolman stores Orca preset names in `extra.orca_profiles`
(double-JSON-encoded). Only the Spoolman plugin encodes/decodes it; core reads `InvMaterial.profile_links` and writes via
`set_profile_links` (drift repair in `laminus.py` confirm-remap; the catalog drift check needs `PROFILE_LINKS_READ`).

Neutral API: `/api/v1/inventory/{materials,spools,sync-now,sync-status,resolve-label,settings}`, `PATCH …/materials/{ref}/profile-links`, and library management (`POST/PATCH /materials[/{ref}]`, `POST /materials/{ref}/archive`, `POST/PATCH /spools[/{ref}]`, `POST /spools/{ref}/archive`, `PUT /spools/{ref}/remaining`; 409 `capability_unavailable` without `MANAGE_*`/`WRITE_WEIGHT`)
(scopes `inventory:read/write`); plugin management `/api/v1/plugins…` and `PUT /api/v1/extension-slots/{kind}` (scopes `settings:*`).

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
(httpx `AsyncClient`) and is awaited through `host.call` (never from the queue loop itself: the completion path schedules
a fire-and-forget task). Per-call `timeout` overrides exist so existing short timeouts
(health 2 s, fingerprint 10 s, catalog health 5 s) are preserved; defaults (client 630 s, poll 620 s) are
unchanged.

### Catalog service

`services/catalog_service.py` owns the process-wide catalog cache (`get_cached_catalog() -> Catalog`,
`warm()`, `refresh(session)`, `rescan(session)`, `status()`, the 30 s health memo and the pending drift
remap). `routes/laminus.py` is a thin HTTP layer over it; nothing else imports a route module. It raises
`CatalogUnavailable(detail, status)` (503 unconfigured / 502 unreachable / 504 rescan timeout) that routes
map to `HTTPException`.

## Adding a provider

**Slicing:** add `providers/<name>/adapter.py` implementing `SlicingProvider` (set the capability flags) plus any vendor client next to
it, a `providers/<name>/__init__.py` calling `register_slicing_provider`, one entry in the accessor in `providers/slicing.py`, and the
package in `ADAPTER_PACKAGES` of `test_provider_boundary.py`.

**Filament inventory:** a plugin — `app/plugins/<id>/` exporting `MANIFEST` (`PluginManifest`: id, kind `filament_inventory`, `settings_model`,
`secret_fields`, `factory`, `capabilities`, `ui`), a provider class implementing the ABC, one entry in `plugins.BUNDLED_MODULES`, the
package in `ADAPTER_PACKAGES`, and a parameter in the provider contract suite
(`tests/plugins/test_filament_inventory_contract.py`). Map vendor errors to `InventoryProviderError`. No core file changes.

## Testing

- `backend/tests/fake_providers.py`: `FakeInventoryProvider`, `FakeSlicingProvider` (records `calls`,
  `fail_with` / `fail_on[method]`, canned `estimates` / `merged` / `health_script`). Patch the accessor **in the
  module under test** (inventory: `await use_provider(fake)` from `tests/inventory_helpers.py` makes it the active provider; the plugin host is configured per test by `session_factory`);
  the accessor reads `config.get_laminus_sidecar_url()` at call time, so `patch("app.config.get_laminus_sidecar_url")`
  also still works.
- Adapter tests run against the real wire formats: Spoolman via `spoolman_upstream` (the real
  `tests/spoolman_mock.py` app behind an httpx transport), Laminus via a mock `httpx.Client` transport
  (`sidecar` fixture in `test_slicing_provider_contract.py`).
- `tests/catalog_helpers.py` primes/inspects the catalog cache (`prime_catalog`, `cached_raw`,
  `patch_cached_catalog`).
