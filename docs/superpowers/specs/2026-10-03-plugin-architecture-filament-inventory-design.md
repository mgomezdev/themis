# Plugin architecture — MVP: filament inventory as a plugin (Spoolman extracted) — design

**Date:** 2026-10-03
**Status:** Draft — for discussion. Not approved; no implementation plan yet.

## Goal

Replace baked-in integrations with a plugin-style architecture. The MVP, and the case used to prove the
design, is filament inventory: **Spoolman stops being part of the core and becomes one *filament
inventory provider* plugin.** The core knows "there may be an inventory of spools and materials," not
"there is a Spoolman."

Success criteria:
1. With no inventory plugin enabled, Themis works the way it does today with Spoolman disabled (type/color
   asks, manually entered loaded filament).
2. With the Spoolman plugin enabled, every current Spoolman feature still works (see the inventory below),
   and existing installs migrate without anyone re-entering anything.
3. A second inventory provider can be added **without touching core code, the queue, or core screens**.
   That means adding a plugin package and one registry entry, the same promise `printer_client_factory`
   makes for printer vendors.
4. The plugin host (manifest, registry, config storage, settings UI, lifecycle) is general enough to take a
   second *kind* of plugin later. This design does not migrate any second kind.

## Non-goals (MVP)

- Installing third-party plugins at runtime (pip/entry points, uploading a zip). MVP plugins ship in-tree.
  The manifest shape keeps runtime installation additive later (see Phase 3).
- Frontend plugins loaded at runtime (Module Federation, iframes). Plugin UI in MVP is either generic UI
  generated from a schema or in-tree React components keyed by plugin id.
- Several inventory providers active at once. Exactly one, or none (see §3.4).
- Moving printer vendors, notifications, Laminus, or the slicer onto the plugin host. Phase 3 candidates.
- A general event bus. MVP extension points are explicit interface calls. A bus is the scaling path once a
  second plugin kind needs it.

---

## 1. What "Spoolman is baked in" means today (blast radius)

Audit of `develop` @ `877c25f`. Every item below is Spoolman-specific, and the design must keep, generalize,
or deliberately drop each one.

### Backend

| Area | Where | What it does |
|---|---|---|
| HTTP client | `services/spoolman_service.py` | `fetch_spools`, `fetch_filaments`, `fetch_filament`, `patch_filament` (writes `extra.orca_profiles`, double-JSON-encoded), `record_spool_use` (`PUT /spool/{id}/use`), `test_connection` |
| Config storage | `models.SpoolmanConfig` / table `spoolman_config` (singleton id=1) | `enabled`, `url`, `api_key`, `sync_interval_minutes`, sync-health columns (v020), low-stock thresholds + alerted set (v026) |
| Background sync | `services/spoolman_sync.py` (`spoolman_sync_loop`, started/stopped in `main.py` lifespan) | Polls every 60 s, syncs per `sync_interval_minutes`, records health, runs low-stock alerts |
| Low-stock alerts | `services/spool_alerts.py` | Emits `spool.low` (webhook + notifications). Thresholds keyed by **Spoolman filament id** |
| Preflight warning | `services/spool_check.py`, `api/routes/queue.py`, `api/routes/jobs.py` | Reads a raw Spoolman spool dict (`remaining_weight`, `filament.name/material`) to produce `low_stock_warning` |
| Usage deduction | `queue_engine.py` (`_deduct_spool`, completion path ~L1216–1305), `jobs.py` manual completion (~L1133) | Reads `SpoolmanConfig` directly, takes `slot["spoolman_spool_id"]`, fire-and-forget deduction |
| Slot ↔ spool link | `printers.loaded_filaments[].spoolman_spool_id` (JSON), preserved across AMS reports in `printer_manager.py:218–235` | Ties a physical slot to a Spoolman spool |
| "Specific filament" ask | `filament_id INTEGER` on `job_printer_configs`, `job_model_targets`, project items. Flows through `jobs.py`, `orders.py`, `projects.py`, `model_targets.py` | Stores a **Spoolman filament id**. Note: the queue does **not** use it for eligibility; matching is type/color only (`_find_slot_for_filament`). It is stored and displayed, and projects group by it |
| Orca profile links | Spoolman filament `extra.orca_profiles`; `api/routes/spoolman.py` PATCH; `catalog_utils.compute_drift` + `laminus.py` confirm | Spoolman is the store for "this material uses these Orca filament presets per printer preset", and catalog drift repairs it |
| Routes | `/api/v1/spoolman/{filaments,spools,sync-now,sync-status,filaments/{id},low-stock}`, `/api/v1/settings/spoolman{,/test}` | 8 OpenAPI paths |
| Auth scopes | `auth.py`: `spoolman:read`, `spoolman:write` | API keys may already be issued with these |
| Webhook event | `spool.low` | External consumers may depend on its payload |

### Frontend

`api/spoolman.ts` (Spoolman-shaped types `ApiSpool`/`ApiFilament`, `parseOrcaProfiles`, `parseSpoolCode`
(a Spoolman QR format regex), `slotPatchForSpool`, hooks), `SpoolmanStatusChip`, `Sidebar` (a "Spoolman" nav
item, plus "Filament Mappings" when enabled), `SettingsScreen` `SpoolmanPage` with its logo,
`SpoolmanMappingsPage`, `LowStockSettings`, `ScanSpoolModal`, `SlotSpoolPicker`, `FilamentRequirementPicker`
("pick from Spoolman"), `PerPrinterConfig`, `RemapModal`, and the Fleet / Printers / Project / Job / Order
screens.

**64 test/source files** under `backend/tests`, `frontend/src`, and `frontend/e2e` mention Spoolman, which
gives a sense of the churn.

### Pre-existing wart found during the audit

`SpoolmanPage` renders three toggles: "Push usage on job events", "Deduct grams from spools", and "Mirror
vendor & material catalog". They live **only in React state**. `SpoolmanConfigIn` has no such fields, so they
persist nothing and change nothing, and deduction always runs when Spoolman is enabled. The migration must
either wire them up or delete them (§5.3). Recommendation: keep "deduct on completion" as a real generic
setting and delete the other two.

---

## 2. Approaches considered

| | A. In-process plugin packages + registry (**recommended**) | B. Out-of-process plugins (HTTP/sidecar contract) | C. Generic "inventory webhook" adapter only |
|---|---|---|---|
| Shape | `app/plugins/<id>/` Python package exporting a manifest. Core holds a registry dict and calls a typed ABC | Each provider is a separate service that speaks a Themis-defined REST contract. Core is the client | Core speaks one fixed REST shape. Users write glue themselves |
| Fits codebase | Yes. Same pattern as `AbstractPrinterClient` + `printer_client_factory` | Partly. Laminus is already a sidecar | Weak. Pushes all the work onto users |
| Third-party authoring | Python, in-tree until Phase 3 entry points | Any language, any time | Any language |
| Isolation | None. A plugin runs with full process trust, so a bug can hang the event loop | Strong. Crash or hang is contained | Strong |
| Ops cost for users | Zero (ships in the image) | Another container per provider | User-built glue |
| Latency / failure modes | Function call | Network hop, versioning, auth | Same as B |

**Recommendation: A**, with B kept possible. Write the provider ABC so that a future
`HttpInventoryProvider` plugin (an adapter that implements the ABC by calling a remote service) turns B into
just one more A-plugin. That gives the cheap path now and the isolated path later, without the core
choosing.

---

## 3. Design

### 3.1 Plugin host (generic, kind-agnostic)

```
backend/app/plugins/
  __init__.py          # registry: dict[str, PluginManifest]; get_plugin(id); plugins_of_kind(kind)
  host.py              # lifecycle: load enabled plugin configs, instantiate, start/stop, health
  manifest.py          # PluginManifest dataclass
  kinds/
    filament_inventory.py   # the FilamentInventoryProvider ABC + neutral DTOs (§3.2)
  spoolman/            # the extracted plugin
    __init__.py        # MANIFEST
    client.py          # was services/spoolman_service.py
    provider.py        # SpoolmanProvider(FilamentInventoryProvider)
    settings.py        # pydantic SpoolmanSettings (url, api_key[secret], sync_interval_minutes)
    labels.py          # web+spoolman:s-<id> parsing (moved from the frontend)
```

```python
@dataclass(frozen=True)
class PluginManifest:
    id: str                      # "spoolman" — stable, persisted, never renamed
    name: str                    # "Spoolman"
    kind: str                    # "filament_inventory"
    version: str                 # plugin's own version
    host_api: int                # host API version it targets; host refuses mismatches
    settings_model: type[BaseModel]   # drives validation AND the generated settings form
    secret_fields: frozenset[str]     # write-only; never returned by the API (today: api_key)
    factory: Callable[[BaseModel], Any]  # settings -> provider instance
    capabilities: frozenset[str]
    description: str = ""
    docs_url: str | None = None
```

Registry is a plain dict, the same as the printer factory: adding a plugin means one package and one
registry line. No import-time scanning magic in MVP.

**Storage.** New table `plugin_configs`:

| column | type | notes |
|---|---|---|
| `plugin_id` | TEXT PK | manifest id |
| `enabled` | BOOL | |
| `settings` | JSON | validated against `settings_model` on write, non-secret fields |
| `secrets` | JSON | secret fields only. Never serialized out |
| `state` | JSON | plugin-owned runtime state (sync health, cursors) |
| `updated_at` | TEXT | |

Plus one row per extension-point slot in a new `extension_slots(kind TEXT PK, plugin_id TEXT NULL)` table.
`filament_inventory → 'spoolman' | NULL` says which provider is active.

**Lifecycle.** `PluginHost.start()` runs in the `main.py` lifespan where `spoolman_sync_loop` starts today.
It instantiates the active provider, and `stop()` tears it down. Changing settings or the active provider
through the API rebuilds the instance in place, so no restart is needed (same UX as today).

**Failure containment (a hard rule, enforced by the host and not left to plugins).** Every provider call
from core goes through `host.call(kind, method, *args, timeout=…)`. It wraps the call with
`asyncio.wait_for`, catches every exception, logs it with the plugin id, records it in `state.last_error`,
and returns a typed failure. The queue loop and request handlers **never** see a plugin exception. This
puts today's ad hoc `try/except: logger.warning("Spoolman unreachable…")` sites in one place. Provider
methods are `async`. A plugin wrapping a blocking SDK must use `run_in_executor`, the same constraint
`docs/agent/backend-review.md` already puts on the queue loop.

### 3.2 Extension point: `filament_inventory`

Neutral DTOs. Core and frontend only ever see these, never a provider's raw JSON:

```python
@dataclass
class InvMaterial:          # Spoolman "filament"; another system's "product"/"SKU"
    ref: str                # provider-opaque id, string even when the provider uses ints
    name: str
    material: str | None    # "PLA", "PETG" — compared against job type asks
    color_hex: str | None   # "#RRGGBB"
    vendor: str | None
    density: float | None
    diameter: float | None
    profile_links: dict[str, list[str]] | None  # {orca printer preset: [orca filament presets]}

@dataclass
class InvSpool:
    ref: str
    material_ref: str | None
    material: InvMaterial | None  # denormalized for display
    remaining_g: float | None     # None = provider doesn't track weight
    location: str | None
    label: str                    # display name
    archived: bool = False
```

```python
class FilamentInventoryProvider(ABC):
    capabilities: frozenset[str]

    async def test_connection(self) -> ConnectionInfo: ...          # required
    async def list_materials(self) -> list[InvMaterial]: ...        # required
    async def list_spools(self) -> list[InvSpool]: ...              # required

    # Optional, gated by capability flags (same idiom as printer capability flags):
    async def record_usage(self, spool_ref: str, grams: float) -> None: ...          # CAP_RECORD_USAGE
    async def set_profile_links(self, material_ref: str, links: dict) -> InvMaterial: ...  # CAP_PROFILE_LINKS_WRITE
    def parse_label(self, text: str) -> str | None: ...              # CAP_LABEL_SCAN -> spool_ref
    def material_url(self, ref: str) -> str | None: ...              # deep link to provider UI
    def spool_url(self, ref: str) -> str | None: ...
```

Capabilities: `RECORD_USAGE`, `TRACKS_WEIGHT`, `PROFILE_LINKS_READ`, `PROFILE_LINKS_WRITE`, `LABEL_SCAN`.
Core and UI branch **only on capabilities, never on `plugin_id == "spoolman"`**. A lint/grep test in the suite
enforces this. See §6.

Read caching: the host keeps the last successful `list_spools`/`list_materials` result (in memory, with
a timestamp). Today every queue list and job detail call does a live `fetch_spools()`. That cost stays
the same in MVP, but the cache gives a degraded read when the provider is down. Is that a behavior
change worth making? See open question Q4.

### 3.3 What moves to core vs. stays in the plugin

The test applied to each item: **is it a print-farm concept, or a Spoolman concept?**

| Concern | Lives in | Why |
|---|---|---|
| Low-stock thresholds, `spool.low` event, "alert once until refilled" | **Core** (`services/inventory/alerts.py`) | Farm policy. Works for any provider with `TRACKS_WEIGHT` |
| Preflight "not enough filament" warning | **Core** (`spool_check` on `InvSpool`) | Same |
| Deduct on job completion (both completion paths) | **Core** decides when and how much. Plugin `record_usage` does the write | Completion semantics belong to the queue; the write belongs to the provider |
| Periodic sync loop + health (`last_sync_at`, error code) | **Core** (generic loop over the active provider) | Every remote provider needs it. Health is stored in `plugin_configs.state` |
| Slot ↔ spool link | **Core** data (`loaded_filaments[].inventory`, §4.2) | Physical slot is a core concept |
| "Specific material" job ask | **Core** data (`material_ref` + provider id) | Job spec is core |
| Orca profile links storage | **Plugin** if `PROFILE_LINKS_*`, else nowhere (Phase 2: core-side fallback table) | Spoolman stores them in `extra`. Other systems may not support them |
| Catalog-drift repair of profile links (Laminus) | Core drift logic. Writes go through `set_profile_links` | Drift is about Orca, not about Spoolman |
| HTTP, auth headers, `extra` double-encoding, QR format | **Plugin** | Pure Spoolman detail |

### 3.4 Only one active provider

`extension_slots.filament_inventory` holds 0 or 1 plugin id. Several providers at once would need every
spool picker to merge lists and every ref to carry a provider id in the UI. That is real complexity for a
case (two inventory systems in one farm) we have no evidence anyone needs. Refs are still **stored with
their provider id** (§4), so switching providers is safe and a multi-provider future isn't blocked.

### 3.5 API surface

New, provider-neutral:

```
GET    /api/v1/plugins                                  # manifests + enabled + active-per-kind (no secrets)
GET    /api/v1/plugins/{id}                             # settings (secrets as has_<field>), JSON schema, state/health
PUT    /api/v1/plugins/{id}                             # update settings/enabled (secrets write-only, omit = keep)
POST   /api/v1/plugins/{id}/test                        # test_connection with supplied-or-saved settings
PUT    /api/v1/extension-slots/filament_inventory       # {plugin_id | null}

GET    /api/v1/inventory/materials
GET    /api/v1/inventory/spools
POST   /api/v1/inventory/sync-now
GET    /api/v1/inventory/sync-status                    # includes provider id + name
PATCH  /api/v1/inventory/materials/{ref}/profile-links  # 409 if provider lacks PROFILE_LINKS_WRITE
POST   /api/v1/inventory/resolve-label                  # {text} -> InvSpool | 404 ; replaces frontend parseSpoolCode
GET/PUT /api/v1/inventory/low-stock
```

Scopes: `inventory:read`, `inventory:write`, `plugins:read`, `plugins:write` (the last two are admin-ish;
see `settings:*` today).

**Compatibility shims (one release, then removed):** `/api/v1/spoolman/*` and `/api/v1/settings/spoolman*`
stay as thin aliases that translate to the new routes and return the **old** response shapes. API keys that
hold `spoolman:read/write` are treated as having `inventory:read/write`. Migration v033 also rewrites the
stored scopes (§4). The aliases are marked `deprecated=True` in OpenAPI. Why bother: API keys exist
specifically for home-automation/QR integrations (`CLAUDE.md` ready-for-work gate). Some of those may call
the Spoolman routes, and breaking them silently on upgrade is the worst outcome.

`spool.low` webhook payload: **unchanged keys**, plus new `provider` and `spool_ref`. `spool_id` stays as an
alias, as an int when it parses as one, for one release.

---

## 4. Data migration (v033, v034)

Ordering matters because of SQLite. Changing `filament_id INTEGER` to TEXT in place needs a table rebuild,
and `job_printer_configs` has the FK/partial-index history that v025/v031 already worked around. So the
plan is **add, backfill, switch readers, drop later**, not alter-in-place.

**v033_plugin_host**
1. Create `plugin_configs` and `extension_slots`.
2. Copy `spoolman_config` row 1 to `plugin_configs('spoolman')`: `{url, sync_interval_minutes}` into
   `settings`, `{api_key}` into `secrets`, sync-health columns into `state`. `enabled` is copied.
3. `extension_slots('filament_inventory')` is `'spoolman'` if `spoolman_config.url` is non-null (even when
   disabled, so it stays selected and the user's config isn't lost), else NULL.
4. Low-stock: create `inventory_config` (singleton) with `low_stock_default_g`, `low_stock_overrides`
   (keys re-namespaced `"<filament_id>"` → `"spoolman:<filament_id>"`), `low_stock_alerted` (same
   namespacing), and `deduct_on_complete BOOL DEFAULT 1`.
5. API key scopes: append `inventory:*` wherever `spoolman:*` is present (keep the old scope string too).
6. **Do not drop `spoolman_config`** in v033. That keeps a downgrade path for one release.

**v034_inventory_refs**
1. `printers.loaded_filaments` JSON: for every slot with `spoolman_spool_id`, add
   `"inventory": {"provider": "spoolman", "spool_ref": "<id>"}`. Leave `spoolman_spool_id` in place for one
   release (old frontend tabs / downgrade). Readers switch to `inventory`.
2. Add `material_provider TEXT NULL`, `material_ref TEXT NULL` to `job_printer_configs`,
   `job_model_targets`, and the project items table. Backfill `('spoolman', CAST(filament_id AS TEXT))`
   where `filament_id IS NOT NULL`. Readers switch over; `filament_id` becomes write-dead and is dropped
   in a later cleanup migration.

**Dangling refs when switching provider.** A stored ref whose `provider` ≠ the active provider (or with
no provider active) is **unresolved**. It shows in the UI as a degraded chip ("Spoolman spool #12 —
provider not active"), the same treatment `SlotSpoolPicker` already gives a vanished spool
(`isDegraded`). It is never matched against the new provider's ids, because int id 12 in Spoolman ≠ id 12
anywhere else. It doesn't block printing (the queue never used it for eligibility). It does skip
deduction, with a log line.

---

## 5. Consequences

### 5.1 Technical

**Gains**
- One seam instead of ~10 scattered `session.get(SpoolmanConfig, 1)` + `enabled and url` checks
  (`queue.py`, `jobs.py` ×3, `queue_engine.py`, `catalog_utils.py`, `laminus.py`, `settings.py`,
  `spoolman.py`). Today those checks are inconsistent: some test `enabled`, laminus confirm doesn't.
- Failure handling for external providers in one place. Today each `spoolman_service` call sets its own
  httpx timeout (5–10 s) and each call site writes its own try/except. Under the host, a third-party plugin
  can't forget either.
- A second inventory provider becomes a self-contained PR. The host is reusable for the next plugin kind.
- Spoolman-format code (the QR regex in the frontend, `extra` double-encoding, raw dict shapes in
  `spool_check`) leaves the core.

**Costs and risks**
- **Large mechanical diff.** About 64 files of tests and source, 8 OpenAPI paths, a new contract surface. It
  needs to be phased (§7) or review quality collapses.
- **Two migrations touching hot tables** (`printers.loaded_filaments`, `job_printer_configs`). They need
  real-data tests (an upgraded copy of a seeded v032 DB) and not just fresh-schema tests.
- **Indirection tax.** Reading "what happens to a spool on job complete" now crosses core → host →
  provider. The docs/agent set must describe the seam, or agents and humans will re-derive it every time.
- **Abstraction designed from n=1.** The ABC is shaped by Spoolman. The risk is a "generic" interface that
  only Spoolman can implement. Mitigation: before freezing the interface, sketch on paper a second provider
  ourselves (e.g. a built-in *Local inventory* provider stored in Themis's own DB, or Bambu AMS RFID
  read-only) and check it fits. Recommendation: build *Local inventory* in Phase 2. It serves users who
  want inventory without running Spoolman, and it is the honest test of the interface.
- **Compat shims carry dead weight** for one release, and someone has to remember to remove them. Put a
  dated removal item on the tracker.
- **No sandbox.** In-process plugins have full trust. That is fine for in-tree code, but it must be a
  conscious decision before Phase 3 (third-party install). See §8.
- **Frontend contract drift risk rises.** New neutral types must be added to `contracts/response-keys.json`.
  Nested keys (e.g. `InvSpool.material.color_hex`) are *not* covered by that contract (CLAUDE.md), so they
  need explicit byte-for-byte checks in review.
- **Coverage ratchets.** Moving code changes per-file coverage. Floors must not be lowered (CLAUDE.md), so
  new plugin code needs its own tests up front.

### 5.2 User experience

| Today | After MVP | Severity |
|---|---|---|
| Sidebar: **Settings → Spoolman** (+ **Filament Mappings** when enabled) | **Settings → Integrations** (plugin list) and **Settings → Filament inventory** (provider picker + that provider's settings form). "Filament Mappings" shows only if the active provider has `PROFILE_LINKS_*` | Medium. Muscle memory and existing docs/screenshots break. Add a one-time "Spoolman settings moved" hint, or redirect the old route `/settings/spoolman` |
| Spoolman settings page with logo + hand-built form | Generated form from `settings_model` JSON schema, with an in-tree override component per plugin allowed (Spoolman keeps its logo/look) | Low, if the override is used |
| Three toggles that do nothing | One real toggle: "Deduct usage on job completion" | Positive, but a visible change. Users who flipped "Deduct" off believing it worked will now see deductions resume *unless* the migration defaults it to on (which matches actual current behavior). Note in release notes |
| Status chip "Spoolman ✓" | "Inventory: Spoolman ✓" (provider name shown). Hidden when no provider | Low |
| "pick…" from Spoolman on job/project filament ask | "pick…" from the active provider. Hidden when none | None |
| Scan spool (Fleet) parses Spoolman QR in-browser | Sends text to `resolve-label`. Button shows only with `LABEL_SCAN` | None for Spoolman users. One more round-trip |
| Upgrading: nothing to do | Nothing to do. Migration carries config, links, thresholds | Must hold, or it's a regression |
| Switching from Spoolman to another provider | New capability. Existing slot/job links show as "from Spoolman (inactive)" until reassigned. Nothing deleted | New behavior, needs clear copy |
| External API users of `/api/v1/spoolman/*` | Works for one release with deprecation header, then breaks | Medium for anyone integrating. Release-note it twice |

What users **don't** see change: queue eligibility, slicing, printing, and costs. Today's
`job_costs.filament` is manual and "never computed from Spoolman pricing", and that stays true.

### 5.3 The dead-toggles decision (needs your call, Q2)

Recommended: delete "Push usage on job events" and "Mirror vendor & material catalog". The first is
redundant with deduction, and the second was never built and implies a feature we'd have to design. Make
"Deduct grams from spools" real as `inventory_config.deduct_on_complete` (default on, which matches what
actually happens today).

---

## 6. Testing strategy

- **Provider contract suite** (`tests/plugins/test_inventory_provider_contract.py`), parametrized over every
  registered `filament_inventory` plugin. It runs each against its fake (Spoolman: the existing
  `tests/spoolman_mock.py`) and asserts the ABC contract: refs are strings, capability flags match the
  implemented methods, `record_usage` without the capability raises `NotSupported`, and DTO fields are
  populated. A new provider has to pass it to merge. This mirrors the virtual-printer pattern for hardware
  protocols.
- **Host containment tests:** a provider that raises, or hangs past timeout, in each call site (queue list,
  job detail, completion deduction, sync loop). Asserts the request still succeeds, the queue still
  advances, and the `state.last_error` row is written. These must fail if the `host.call` wrapper is
  bypassed.
- **"No Spoolman in core" guard:** a test greps `backend/app` outside `app/plugins/spoolman/` and
  `frontend/src` outside the Spoolman override component for `spoolman` (case-insensitive), allowing only the
  compat shim modules and migrations. It fails the build on regression. Cheap, and it is the only thing that
  keeps the boundary from eroding.
- **Migration tests:** seed a v032 DB with a configured Spoolman, slots with `spoolman_spool_id`, jobs and
  project items with `filament_id`, thresholds with overrides, alerted set, and API keys with
  `spoolman:read`. Run v033+v034, then re-read every row and assert the new shape. Also assert the old
  columns and keys still exist (downgrade window).
- **Compat shim tests:** every old route returns its old response shape byte-for-byte against a fixture
  captured from today's routes **before** the refactor starts. Capture it in Phase 0.
- **Frontend:** `api/inventory.ts` tests via `stubFetch`, capability-gating tests (no provider: no picker,
  no chip, no scan button; provider without `LABEL_SCAN`: no scan button), generated settings form
  (secret field write-only, `has_api_key` display), and moved-route redirect. Update e2e `mock-api.ts`.
- Regenerate `openapi.json`. Update `contracts/response-keys.json`.

---

## 7. Phasing

Each phase is a separate PR into `develop`, shippable on its own, and the suite is green at every step.

- **Phase 0 — safety net (no behavior change):** capture golden responses for all 8 Spoolman routes +
  `spool.low` payload; add the migration-test fixture DB at v032; decide Q1–Q6.
- **Phase 1 — host + extension point, Spoolman behind it, compat kept:** plugin host, `plugin_configs`,
  `extension_slots`, ABC + DTOs, `plugins/spoolman/` (move `spoolman_service`), core inventory services
  (alerts, preflight, deduction, sync loop) rewritten against DTOs, v033 + v034, new `/inventory` +
  `/plugins` routes, old routes become shims. Frontend still uses old routes. **Users see nothing
  change.**
- **Phase 2a — frontend cutover:** `api/inventory.ts`, capability-gated components, Integrations + Filament
  inventory settings, generated settings form with Spoolman override, route redirect, dead toggles
  resolved, `resolve-label`. "No Spoolman in core" guard turned on.
- **Phase 2b — second provider (*Local inventory*):** proves the interface. Adjust the ABC if it doesn't
  fit, while changing it is still cheap.
- **Phase 2c — cleanup (after one release):** drop shims, `spoolman_config`, `filament_id` columns,
  `spoolman_spool_id` slot key, `spoolman:*` scopes.
- **Phase 3 (separate design):** entry-point discovery for pip-installed plugins, a second plugin *kind*
  (candidates: notification channels, Laminus/catalog source, printer vendors), and maybe an event bus.

---

## 8. Assumptions (correct me where wrong)

1. **"Plugin" means a user-selectable, swappable integration, not runtime-installable third-party code**,
   at least for MVP. If you mean "anyone can drop a plugin in without a Themis release", Phase 3 is the
   real goal and the security posture (§5.1, no sandbox) must be decided first.
2. **One inventory system per farm.** Nobody runs Spoolman for some printers and something else for others.
3. **The queue keeps matching on type/color.** A "specific material" ask stays informational, like today. If
   you want a hard "must be this exact material/spool" constraint, that is a separate feature (and a
   bigger one).
4. **Profile links can live in the provider.** If a user picks a provider with no profile-link support,
   losing "auto-pick Orca preset when a spool is loaded" is acceptable until a core-side fallback table
   exists (Phase 2b's Local inventory would give it).
5. **Existing installs must upgrade with zero manual steps**, and a one-release downgrade window is wanted.
6. **External API consumers of `/api/v1/spoolman/*` may exist** (API keys + QR/home-automation use case), so a
   one-release deprecation window is worth its cost.
7. **The frontend stays one bundle.** Plugin UI is generated or in-tree. No remote React loading.
8. **Spoolman remains the default and recommended provider** in docs and in the Docker compose example.
   Nothing in the UX demotes it.
9. **In-process trust is acceptable** because every plugin is reviewed in-tree.
10. `job_costs.filament` stays manual. Pulling price from the inventory provider is out of scope.

## 9. Open questions

- **Q1.** Is Assumption 1 right: in-tree plugins for MVP, third-party installation later?
- **Q2.** Dead toggles (§5.3): delete two and make "deduct on completion" real?
- **Q3.** Phase 2b: build *Local inventory* as the second provider, or something else (Bambu AMS RFID
  read-only, OctoPrint SpoolManager, SimplyPrint)? Something has to be second, or the abstraction is
  unproven.
- **Q4.** Serve the last-known spool list when the provider is down (stale but useful), or keep today's
  behavior of showing nothing?
- **Q5.** Settings IA: one "Integrations" page listing all plugins, or per-kind pages ("Filament inventory")?
  This design proposes both: Integrations as the catalog, and kind pages for the active choice.
- **Q6.** Compat window length: one release, or until a fixed date?

## 10. Docs to update when implemented

`CLAUDE.md` (Key design patterns: a "Plugin host" paragraph; Database: new tables, Spoolman table
retirement), `docs/agent/backend.md`, `docs/agent/data-model.md`, `docs/agent/frontend.md`, the
`backend-review.md` checklist (a new item: "core code never branches on plugin id; provider calls go through
`host.call`"), and a new `docs/plugins.md` authoring guide parallel to `docs/printer-interface.md`. Use
`themis-docs-sync`.
