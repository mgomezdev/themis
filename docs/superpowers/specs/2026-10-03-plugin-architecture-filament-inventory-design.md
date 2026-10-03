# Plugin architecture — filament inventory as the first plugin (Spoolman extracted) — design

**Date:** 2026-10-03
**Status:** Ideation. This doc explores the shape of the change and is not a committed scope. No
implementation plan exists yet. Decisions recorded below (§11) are the ones the owner has made so far.
Everything else is a proposal.

## Goal

Replace baked-in integrations with a plugin-style architecture. Filament inventory is the first case and the
one used to prove the design: **Spoolman stops being part of the core and becomes one *filament inventory
provider* plugin.** The core knows "there may be an inventory of spools and materials," not "there is a
Spoolman."

Success criteria:
1. With no inventory plugin active, Themis works the way it does today with Spoolman disabled (type/color
   asks, manually entered loaded filament).
2. With the Spoolman plugin active, every current Spoolman feature still works (inventory in §1), and
   existing installs migrate with no manual steps.
3. A second provider (**Local inventory**, §3.6, decided) can be added without touching core code, the queue,
   or core screens.
4. The plugin host (manifest, registry, config storage, UI contributions, lifecycle, plugin-owned tables) is
   general enough to take a second *kind* of plugin later.

## Open at ideation stage (not decided)

- **How plugins get installed.** Choices: in-tree only, pip/entry points, or uploaded bundles. That choice
  decides the trust/sandbox posture (§5.1) and whether plugin UI can ship custom React (§3.7). This doc keeps
  the manifest shape neutral so any of those can be chosen later. It assumes in-tree only where a concrete
  assumption is needed to reason about consequences.
- Several inventory providers active at once (§3.4 proposes one).
- A general event bus versus explicit interface calls (§3.2 proposes explicit calls).
- Which plugin kind comes second (printer vendors, notifications, Laminus/catalog source, cameras).

---

## 1. What "Spoolman is baked in" means today (blast radius)

Audit of `develop` @ `877c25f`, verified by a review pass against source. The design must keep, generalize,
or deliberately drop each item.

### Backend

| Area | Where | What it does |
|---|---|---|
| HTTP client | `services/spoolman_service.py` | `fetch_spools`, `fetch_filaments`, `fetch_filament`, `patch_filament` (writes `extra.orca_profiles`, double-JSON-encoded), `record_spool_use` (`PUT /spool/{id}/use`), `test_connection`. Per-call httpx timeouts of 5–10 s |
| Config storage | `models.SpoolmanConfig` / `spoolman_config` (singleton id=1) | `enabled`, `url`, `api_key`, `sync_interval_minutes`, sync-health columns (v020), low-stock default, overrides, and alerted set (v026) |
| Background sync | `services/spoolman_sync.py` (`spoolman_sync_loop`, started and stopped in `main.py` lifespan) | Polls every 60 s, syncs per `sync_interval_minutes`, records health, runs low-stock alerts in a savepoint |
| Low-stock alerts | `services/spool_alerts.py` | Emits `spool.low` (webhook + notifications; payload has `spool_id` **and** `filament_id`). Threshold overrides are keyed by **Spoolman filament id**. The alerted set holds **Spoolman spool ids** |
| Preflight warning | `services/spool_check.py`, `routes/queue.py`, `routes/jobs.py` | Reads a raw Spoolman spool dict and produces `low_stock_warning` (a contract key; carries a Spoolman `spool_id`) |
| Usage deduction | `queue_engine.py` (`_deduct_spool`, completion path ~L1216–1305), `jobs.py` manual completion (~L1133–1161) | Reads `SpoolmanConfig` directly, takes `slot["spoolman_spool_id"]`, and fires the deduction as an `asyncio.create_task` |
| Slot ↔ spool link | `printers.loaded_filaments[].spoolman_spool_id` (JSON). Writers: `PATCH /printers` (replaces the whole list from the client), `printer_manager.on_ams_change` (merges by slot but **only keeps** `filament_profile` + `spoolman_spool_id`), `laminus.py` confirm (rewrites `filament_profile`) | Ties a physical slot to a Spoolman spool. Note: Bambu's `loaded_filaments[].filament_id` is an **AMS tray code**, unrelated to Spoolman |
| "Specific filament" ask | `filament_id INTEGER` on `job_printer_configs`, `job_model_targets`, `project_items`, **plus `orders.parts[].filament_id` in JSON**. Flows through `jobs.py`, `orders.py`, `projects.py`, `model_targets.py`. `project_item.filament_id` is a contract key | Stores a **Spoolman filament id**. The queue does **not** use it for eligibility; matching is type/color only (`_find_slot_for_filament`). It is stored, displayed, and used for project grouping |
| Orca profile links | Spoolman filament `extra.orca_profiles`; `routes/spoolman.py` PATCH; `catalog_utils.compute_drift` + `laminus.py` confirm | Spoolman is the store for "this material uses these Orca filament presets per printer preset", and catalog drift repairs it |
| Routes | `/api/v1/spoolman/{filaments,spools,sync-now,sync-status,filaments/{id},low-stock}`, `/api/v1/settings/spoolman{,/test}` | 8 OpenAPI paths |
| Auth scopes | `auth.py`: `spoolman:read`, `spoolman:write`. The admin login session and browser device key are persisted with a **snapshot** of the scope list (`routes/session.py:50`) | |
| Inconsistent gating | `enabled and url` is checked in `queue.py`, `jobs.py` ×2, `queue_engine.py`, `catalog_utils.py`, `settings.py`, `spoolman.py`; `laminus.py` confirm checks only `url` | |

### Frontend

- `api/spoolman.ts`: Spoolman-shaped types (`ApiSpool`, `ApiFilament`), `parseOrcaProfiles`,
  `parseSpoolCode` (a regex for the Spoolman QR format), `slotPatchForSpool`, and hooks.
- Status and nav: `SpoolmanStatusChip`, and `Sidebar` (a "Spoolman" item, plus "Filament Mappings" when
  enabled).
- Settings: `SettingsScreen` `SpoolmanPage` (with logo), `SpoolmanMappingsPage`, `LowStockSettings`.
- Pickers and modals: `ScanSpoolModal`, `SlotSpoolPicker`, `FilamentRequirementPicker`, `PerPrinterConfig`,
  `RemapModal`.
- Screens: Fleet, Printers, Project, Job, and Order.
- API keys: `api/apiKeys.ts` mirrors the scope list by hand.

About **83 files** across `backend/app`, `backend/tests`, `frontend/src`, and `frontend/e2e` mention Spoolman.

### Pre-existing wart

`SpoolmanPage` renders three toggles ("Push usage on job events", "Deduct grams from spools", "Mirror vendor &
material catalog") that live **only in React state**. Nothing persists them and nothing reads them. Deduction
always runs when Spoolman is enabled. **Decided:** delete the first and third, and make deduct-on-completion a
real setting (§3.3).

---

## 2. Approaches considered

| | A. In-process plugin packages + registry (**proposed**) | B. Out-of-process plugins (HTTP/sidecar contract) | C. Generic "inventory webhook" adapter only |
|---|---|---|---|
| Shape | `app/plugins/<id>/` package exporting a manifest. Core holds a registry and calls a typed ABC | Each provider is a separate service speaking a Themis-defined REST contract | Core speaks one fixed REST shape. Users write glue |
| Fits codebase | Yes. Same pattern as `AbstractPrinterClient` + `printer_client_factory` | Partly (Laminus is already a sidecar) | Weak |
| Isolation | None (full process trust) | Strong | Strong |
| Ops cost for users | Zero | One more container per provider | User-built glue |

A, written so that B stays possible. A future `HttpInventoryProvider` plugin implements the ABC by calling a
remote service, which makes B just one more A-plugin. That also leaves an answer if the install model (open
question above) settles on "untrusted third-party code": run it out of process behind the same ABC.

---

## 3. Design

### 3.1 Plugin host (kind-agnostic)

```
backend/app/plugins/
  __init__.py          # registry: dict[str, PluginManifest]; get_plugin(id); plugins_of_kind(kind)
  host.py              # lifecycle, call wrapper, health, outbox flushing
  manifest.py          # PluginManifest, UiContribution
  migrations.py        # runs each plugin's own migrations (§3.8)
  kinds/
    filament_inventory.py   # FilamentInventoryProvider ABC + DTOs (§3.2)
  spoolman/            # extracted plugin: client.py, provider.py, settings.py, labels.py
  local_inventory/     # new built-in plugin (§3.6): models.py, provider.py, routes.py, migrations/
```

```python
@dataclass(frozen=True)
class PluginManifest:
    id: str                       # "spoolman" — stable, persisted, never renamed
    name: str
    kind: str                     # "filament_inventory"
    version: str
    host_api: int                 # host API version targeted; host refuses mismatches
    settings_model: type[BaseModel]    # validation + generated settings form (JSON schema)
    secret_fields: frozenset[str]      # write-only; never returned by any API
    factory: Callable[[BaseModel], Any]
    capabilities: frozenset[str]
    ui: UiContribution = UiContribution()   # §3.7
    routers: tuple[APIRouter, ...] = ()     # mounted under /api/v1/plugins/{id}/…
    migrations: tuple[ModuleType, ...] = () # §3.8
    description: str = ""
    docs_url: str | None = None
```

The registry is a plain dict, so adding a plugin means one package and one registry line.

**Storage** (core tables):

| table | columns | purpose |
|---|---|---|
| `plugin_configs` | `plugin_id` PK, `enabled`, `settings` JSON, `secrets` JSON, `state` JSON, `updated_at` | per-plugin config and runtime state (sync health, connection status) |
| `extension_slots` | `kind` PK, `plugin_id` NULL | which provider fills a single-provider kind |

**Active rule (one source of truth):** a provider is *active* iff `extension_slots[kind] == id` **and**
`plugin_configs[id].enabled`. Selecting a provider in the slot enables it. Disabling it leaves the slot set,
so the selection survives, but makes it inactive. Core asks only `host.active(kind)`.

**Lifecycle.** `PluginHost.start()`/`stop()` go in the `main.py` lifespan where `spoolman_sync_loop` runs
today. A settings change or provider switch through the API rebuilds the instance in place, with no restart.

**Failure containment (enforced by the host).** Every core → provider call goes through
`host.call(kind, method, …, timeout=…)`. The wrapper runs `asyncio.wait_for`, catches everything, logs with
the plugin id, records `state.last_error`, and returns a typed failure. Plugin exceptions never reach the
queue loop or request handlers. Provider methods are `async`; a plugin wrapping a blocking SDK uses
`run_in_executor`.

**Queue-loop rule** (`backend-review.md` §3): the queue loop **never awaits** a provider call. Writes the
queue triggers (usage deduction) go through the outbox (§3.5): a local DB insert inside the completion
transaction, flushed later by the host's own task. The queue loop does no external I/O at all, which is
stricter than today's `create_task`.

### 3.2 Extension point: `filament_inventory`

Neutral DTOs. Core and frontend only ever see these, never a provider's raw JSON:

```python
@dataclass
class InvMaterial:
    ref: str                 # provider-opaque id, always a string
    name: str
    material: str | None     # "PLA" — compared against type asks
    color_hex: str | None    # "#RRGGBB"
    vendor: str | None
    density: float | None
    diameter: float | None
    profile_links: dict[str, list[str]] | None  # {orca printer preset: [orca filament presets]}

@dataclass
class InvSpool:
    ref: str
    material_ref: str | None
    material: InvMaterial | None
    remaining_g: float | None   # None = provider doesn't track weight
    location: str | None
    label: str
    archived: bool = False
```

```python
class FilamentInventoryProvider(ABC):
    capabilities: frozenset[str]
    async def test_connection(self) -> ConnectionInfo: ...      # required
    async def list_materials(self) -> list[InvMaterial]: ...    # required
    async def list_spools(self) -> list[InvSpool]: ...          # required
    # Optional, gated by capability:
    async def record_usage(self, spool_ref: str, grams: float, *, idempotency_key: str) -> None: ...  # RECORD_USAGE
    async def set_profile_links(self, material_ref: str, links: dict) -> InvMaterial: ...           # PROFILE_LINKS_WRITE
    def parse_label(self, text: str) -> str | None: ...         # LABEL_SCAN -> spool_ref
    def spool_url(self, ref: str) -> str | None: ...            # deep link into the provider's own UI
```

Capabilities: `RECORD_USAGE`, `TRACKS_WEIGHT`, `PROFILE_LINKS_READ`, `PROFILE_LINKS_WRITE`, `LABEL_SCAN`,
`REMOTE` (the provider lives elsewhere and can be unreachable; enables §3.5 offline behavior and the
disconnect-alert setting). **Core and UI branch only on capabilities, never on plugin id.**

`idempotency_key` is the outbox row id. Providers that can dedupe (Local inventory can) use it. Spoolman's
`/use` cannot, which is a real consequence (§5.1).

### 3.3 Core vs. plugin responsibilities

| Concern | Lives in |
|---|---|
| Low-stock thresholds, `spool.low`, "alert once until refilled" | **Core** `services/inventory/alerts.py`, on *effective* remaining (§3.5). Needs `TRACKS_WEIGHT` |
| Preflight "not enough filament" warning | **Core**, on `InvSpool` effective remaining |
| When and how much to deduct (both completion paths) + `deduct_on_complete` setting | **Core** (writes the outbox). Plugin `record_usage` does the write |
| Sync loop, health, last-known cache, outbox flush, disconnect alert | **Core** host, generic over `REMOTE` providers |
| Slot ↔ spool link, "specific material" job ask | **Core** data, with provider-namespaced refs (§4) |
| Orca profile links | Plugin (`PROFILE_LINKS_*`). Drift repair logic stays core and writes through `set_profile_links` |
| HTTP, auth, `extra` encoding, label format | **Plugin** |

Core inventory settings live in a new `inventory_config` singleton: `deduct_on_complete` (default on, which
matches actual behavior today), `low_stock_default_g`, `low_stock_overrides`, and `low_stock_alerted`.

### 3.4 One active provider (proposal)

`extension_slots.filament_inventory` holds 0 or 1 plugin. Refs are still stored with their provider id, so
switching is safe and a multi-provider future isn't blocked.

### 3.5 Offline behavior for remote providers (decided: serve last-known, accrue deltas, alert)

When a `REMOTE` provider is unreachable, Themis keeps working from the last good data and catches up later.

**Last-known cache.** After each successful `list_spools`/`list_materials`, the host persists the result in
`inventory_cache(provider, kind, payload JSON, fetched_at)`. Persisting it means a restart while the provider
is down still has data. Reads (`/inventory/spools`, pickers, preflight, queue list) serve the live result
when it's available and the cache when it isn't. Responses carry `stale: bool` and `as_of`.

**Usage outbox.** `inventory_pending_usage`:

| column | notes |
|---|---|
| `id` PK | also the `idempotency_key` |
| `provider`, `spool_ref`, `grams` | |
| `job_id`, `printer_id`, `source` | `queue` or `manual_complete`, for audit and the UI |
| `created_at`, `attempts`, `last_attempt_at`, `last_error` | |
| `status` | `pending` → `applied` / `discarded` |

- **Every** deduction goes through the outbox, online or not: one code path. The completion transaction
  inserts the row. The host flushes `pending` rows in `created_at` order right after commit (online case:
  effectively immediate) and on every successful sync/reconnect. The queue loop never awaits it.
- **Effective remaining** = cached `remaining_g` − Σ pending grams for that spool. Preflight, low-stock
  alerts, and the pickers use effective remaining, so the numbers stay honest offline. The UI marks pending
  ("412 g · 38 g pending sync").
- Rows for a provider that is no longer active stay `pending`. The provider page lists them with **Apply
  when reconnected** (default) or **Discard**. They are never re-targeted at a different provider.
- After a successful flush the host re-fetches spools, so the cache reflects the provider's own numbers.

**Disconnect alert.** Each `REMOTE` provider's settings get a host-standard field, `max_disconnect_minutes`
(user-set, blank = never alert). The host tracks `state.disconnected_since`. When it is exceeded, the host
emits a new event `inventory.disconnected` (webhook + notification channels, same plumbing as `spool.low`)
once per outage, with `{provider, since, pending_count, pending_grams}`. On recovery it emits
`inventory.reconnected` with the flushed counts. The status chip turns red, and a banner shows on screens
that display spool data ("Spoolman unreachable since 14:02 — showing last-known spools; 3 usage records
pending").

**Ambiguous failures.** A timeout means the outcome is unknown: the provider may have applied the write.
For providers without idempotency (Spoolman), replaying risks a double deduction. Proposed handling: on a
timeout, re-read that spool's `remaining_weight` before retrying. If it already dropped by ≈ `grams`, mark
the row `applied`; otherwise retry. This is a heuristic (another client could have used the spool in the
meantime), so log every decision it makes. Clean failures (connection refused, 5xx before send) retry
without the check.

### 3.6 Local inventory provider (decided: built-in, minimum feature set)

Purpose: inventory without running anything else, and a second implementation that proves the interface.
Scope is **only what current Themis operation consumes**, derived from §1:

| Current Themis use | Local inventory needs |
|---|---|
| Slot spool picker, `slotPatchForSpool` (type, color, name, auto-pick Orca preset) | Materials: name, material type, color, vendor. Spools: material, label/name, location |
| Preflight warning, low-stock alerts, Fleet "g left" | Spools: `initial_g`, `remaining_g` (+ `spool_weight_g` for tare when weighing) |
| Deduction on completion | `record_usage`: atomic decrement, deduped on `idempotency_key` |
| Filament Mappings page / Laminus drift repair | `profile_links` on materials, read and write |
| "Specific material" ask on jobs, projects, orders | Material list (that's all the ask reads) |
| Scan spool (Fleet) | `parse_label` for its own code `themis:s-<id>` (plus bare id). Printing labels is **out** of the minimum |
| Low-stock override per material | Material id as ref (core already handles it) |

**Tables** (plugin-owned, §3.8): `local_inv_materials(id, name, material, color_hex, vendor, density,
diameter, profile_links JSON, archived)`, `local_inv_spools(id, material_id FK, label, location, initial_g,
remaining_g, spool_weight_g, archived, created_at)`, `local_inv_usage(id, spool_id, grams, idempotency_key
UNIQUE, job_id, created_at)`. The usage table is the audit log and enforces dedupe.

**UI** (own nav entry, §3.7): **Spools** (list, add, edit, archive, "set remaining" after weighing, "refill"
which resets to `initial_g`), **Materials** (CRUD and color), **Mappings** (the shared profile-links
component, also used by Spoolman), and **Settings** (the default plugin page).

**Explicitly out of minimum:** price and cost per gram (job costs stay manual), label printing, purchase
history, multiple diameters per material, import from Spoolman. Import is the most likely ask to come next,
so a one-shot "import materials + spools from the active Spoolman" action is a cheap follow-up because both
sides are DTOs.

Capabilities: `RECORD_USAGE`, `TRACKS_WEIGHT`, `PROFILE_LINKS_READ`, `PROFILE_LINKS_WRITE`, `LABEL_SCAN`. Not
`REMOTE`, so there is no cache, outbox delay, or disconnect alert. The outbox still records each deduction
and flushes it immediately.

### 3.7 Plugin UI contributions (decided: plugin-defined pages, shared default page)

A plugin chooses how much UI it needs:

```python
@dataclass(frozen=True)
class UiContribution:
    mode: Literal["section", "page"] = "section"
    # "section": the plugin renders as a collapsible section on the common Settings → Plugins page,
    #            using the default plugin page body. Nothing else.
    # "page":    the plugin gets its own sidebar entry with tabs.
    nav_label: str | None = None
    nav_placement: Literal["settings", "main"] = "settings"  # "main" for operational screens (Local inventory)
    nav_icon: str | None = None                               # from the app's icon set
    tabs: tuple[UiTab, ...] = ()

@dataclass(frozen=True)
class UiTab:
    id: str                  # route: /plugins/{plugin_id}/{tab_id}
    label: str
    renderer: Literal["default", "schema", "component"]
    # default   -> the shared default plugin page (below)
    # schema    -> a generic form/table from a JSON schema the plugin serves at
    #              GET /api/v1/plugins/{id}/ui/{tab_id} (no frontend code needed)
    # component -> a React component registered in-tree under `${plugin_id}/${tab_id}`
```

**Default plugin page** (`PluginSettingsPage`, the one every plugin can reference):
- Header: name, version, description, docs link, enable toggle, and health chip.
- Connection: a form generated from `settings_model`. Secret fields are write-only and show "set ✓ /
  replace". There is a **Test connection** button.
- For `REMOTE` providers: `max_disconnect_minutes`, last sync, last error, cache age, and the pending-usage
  list with apply/discard.
- Danger zone: disable, and "remove plugin data" (asks for confirmation; only for plugin-owned tables).

**Core inventory page** (Settings → Filament inventory, core-owned): active provider picker (None, Spoolman,
Local inventory), `deduct_on_complete`, and the low-stock thresholds. It links to the active provider's own
page.

The sidebar shows a plugin's nav entry only while the plugin is enabled. The old routes `/settings/spoolman`
and `/settings/spoolman-mappings` redirect to the new ones.

Proposed layouts:
- **Spoolman** → `page`, settings placement, tabs: Connection (`default`) and Filament mappings
  (`component`, shared profile-links component).
- **Local inventory** → `page`, main placement ("Inventory"), tabs: Spools, Materials, Mappings
  (`component`), Settings (`default`).
- A trivial plugin → `section`.

The `component` renderer requires in-tree frontend code. That is fine while plugins are in-tree. If the
install model goes third-party, `schema` tabs are the path that needs no frontend code, and `component`
would need remote loading (open question).

### 3.8 Plugin-owned tables and migrations

Local inventory needs its own tables, so the host gets plugin migrations:
- They live in `plugins/<id>/migrations/vNNN_*.py`, using the same `version`/`name`/`up`/`down` shape as
  core.
- They are tracked in a `plugin_schema_versions(plugin_id, version)` table.
- They run after core migrations on startup, **only for plugins in the registry**. A plugin that was never
  enabled still gets its tables, which is simpler and harmless.
- Table names are prefixed with the plugin id. Plugin tables may FK **to** core tables (with
  `ON DELETE SET NULL`). Core tables never FK into plugin tables.
- They follow the core idempotency rules (PRAGMA/`IF NOT EXISTS` guards). These matter because fresh DBs get
  every model from `create_all` in v001.

### 3.9 API surface

```
GET    /api/v1/plugins                                  # manifests, ui contributions, enabled/active (no secrets)
GET    /api/v1/plugins/{id}                             # settings (secrets as has_<field>), JSON schema, state
PUT    /api/v1/plugins/{id}                             # settings/enabled; secrets write-only, omit = keep
POST   /api/v1/plugins/{id}/test
GET    /api/v1/plugins/{id}/ui/{tab}                    # schema-renderer tabs
*      /api/v1/plugins/{id}/…                           # plugin's own routers (Local inventory CRUD)
PUT    /api/v1/extension-slots/filament_inventory       # {plugin_id | null}

GET    /api/v1/inventory/materials | /spools            # + stale, as_of
POST   /api/v1/inventory/sync-now
GET    /api/v1/inventory/sync-status                    # provider, health, disconnected_since, pending counts
GET    /api/v1/inventory/pending-usage ; POST …/{id}/discard ; POST …/flush
PATCH  /api/v1/inventory/materials/{ref}/profile-links  # 409 without PROFILE_LINKS_WRITE
POST   /api/v1/inventory/resolve-label                  # replaces frontend parseSpoolCode
GET/PUT /api/v1/inventory/settings                      # deduct_on_complete + low-stock
```

**Scopes.** Plugin config routes are gated on the existing `settings:read/write`, so admins need no new
scope. Inventory routes use new `inventory:read/write`. The migration grants these to every key holding
`spoolman:*` **or** `settings:write` (precedent: v021). That includes the persisted admin session and device
keys, whose scopes are snapshots. The hand-mirrored `frontend/src/api/apiKeys.ts` SCOPES list is updated too.

**Compat window.** For one release, `/api/v1/spoolman/*` and `/api/v1/settings/spoolman*` stay as aliases
that return the **old** shapes. They are marked `deprecated` in OpenAPI and work only while Spoolman is the
active provider; otherwise they return 409 with a pointer to the new routes. Keys holding `spoolman:*` are
accepted. `spool.low` keeps its `spool_id` and `filament_id` keys (as ints when the ref parses as one) and
adds `provider`, `spool_ref`, and `material_ref`. `low_stock_warning` keeps its shape, with `spool_ref`
added.

---

## 4. Data migration

Core migrations go in this order: **add, backfill, dual-write, switch readers, then drop in a later
release**. Changing `INTEGER` to `TEXT` in place needs an SQLite table rebuild, and `job_printer_configs` has
the FK and partial-index history from v025/v031. All new migrations are idempotent and have a `down()` for the
downgrade window.

**v033_plugin_host**
1. Create `plugin_configs`, `extension_slots`, `plugin_schema_versions`, `inventory_config`,
   `inventory_cache`, and `inventory_pending_usage`.
2. Copy `spoolman_config` → `plugin_configs('spoolman')`: url and interval go to `settings`, `api_key` to
   `secrets`, health columns to `state`, and `enabled` is copied.
3. `extension_slots('filament_inventory')` = `'spoolman'` if `spoolman_config.url` is set, else NULL.
4. `inventory_config`: `deduct_on_complete = 1`, the low-stock default, overrides re-keyed
   `"<filament_id>"` → `"spoolman:<filament_id>"`, and alerted **spool** ids re-keyed to
   `"spoolman:<spool_id>"`.
5. Scope grants (§3.9).
6. `spoolman_config` is **kept**.

**v034_inventory_refs**
1. `printers.loaded_filaments`: for each slot with `spoolman_spool_id`, add
   `"inventory": {"provider": "spoolman", "spool_ref": "<id>"}`. The new key is an object named
   `inventory`, never `filament_id`, which on Bambu already means an AMS tray code.
2. Add `material_provider` and `material_ref` (TEXT NULL) to `job_printer_configs`, `job_model_targets`, and
   `project_items`. Backfill `('spoolman', CAST(filament_id AS TEXT))`.
3. `orders.parts[]` JSON: add `material_provider`/`material_ref` alongside `filament_id`.

**Dual-write during the compat window.** Old frontend tabs, external API clients, and AMS reports keep
writing the old keys, so one normalizer on the server (`inventory/refs.py`) runs on **every writer**:
- **`PATCH /printers` `loaded_filaments`:**
  - The client replaces the whole list. The normalizer derives `inventory` from `spoolman_spool_id` when
    only the old key is sent, mirrors `spoolman_spool_id` from `inventory` when the provider is Spoolman,
    and keeps the existing `inventory` for a slot the body didn't change.
- **`printer_manager.on_ams_change`:** change the merge to preserve **all Themis-owned keys** (an explicit
  set: `filament_profile`, `spoolman_spool_id`, and `inventory`) instead of two named fields. Otherwise every
  Bambu AMS report wipes the link.
- **Laminus confirm:** already spreads `{**loaded[slot], …}`, which is safe. A test pins that behavior.
- **Job/target/project/order writes:** accept `filament_id` or `material_ref`, and write both while the
  provider is Spoolman.

So `filament_id` is **not** write-dead until cleanup.

**Dangling refs on provider switch.** A ref whose provider isn't active is *unresolved*. It shows as a
degraded chip ("Spoolman spool #12 — provider not active"). It is never matched against another provider's
ids and never blocks printing, and its deductions stay in the outbox as `pending` (§3.5).

**Cleanup release (later):**
- Drop the shims, `spoolman_config`, the `filament_id` columns and keys, `spoolman_spool_id`, and the
  `spoolman:*` scopes. A migration strips those scopes from stored keys, because `api_keys.py` rejects unknown
  scopes on edit.
- **Guard old migrations first.** v001's `_ALTERS` (re-adds `job_printer_configs.filament_id`), v020, and
  v026 (`ALTER TABLE spoolman_config`) all assume the old schema. Once the models drop it, they must skip
  when the table or column is absent, or fresh installs break.

---

## 5. Consequences

### 5.1 Technical

**Gains**
- One seam replaces ~9 scattered `SpoolmanConfig` lookups with inconsistent `enabled` checks.
- Failure handling is in one place, and the queue loop never touches external I/O.
- Deductions survive outages and restarts. Today a deduction that fails while Spoolman is down is **lost**
  (log line only). The outbox makes them durable.
- A second provider (Local inventory) proves the interface, and the host is reusable for the next plugin
  kind.

**Costs and risks**
- **Large diff.** About 83 files, 8 OpenAPI paths, new tables, and new events. It must be phased (§7).
- **Hot-table migrations + dual-write.** The normalizer must cover every writer, and missing one silently
  drops links. Tests per writer (§6).
- **Double-deduction risk** when replaying to non-idempotent Spoolman after an ambiguous timeout. This is
  mitigated by the re-read heuristic (§3.5), not eliminated. Today's behavior is the opposite failure (lost
  deductions). Lean toward the new failure, because it's visible in the audit log.
- **Stale-data decisions.** Preflight and low-stock run on cached numbers during an outage. The UI must show
  `stale`/`as_of` wherever a number drives a decision.
- **Indirection tax.** Core → host → provider. The docs/agent set must describe the seam.
- **Interface shaped by n=2,** and both providers are ours. Still better than n=1.
- **Plugin-owned migrations** add a second migration track: ordering, downgrade, and "plugin removed from
  registry but its tables remain" all need handling.
- **No sandbox.** In-process plugins have full trust. This is fine for in-tree code, and it is the deciding
  factor in the install-model question.
- **Contract drift.** New nested DTO keys aren't covered by `contracts/response-keys.json`, so they need
  byte-for-byte review on both sides.
- **Shims and dual-write are dead weight** for one release, and need a dated cleanup item.

### 5.2 User experience

| Today | After | Severity |
|---|---|---|
| Settings → Spoolman (+ Filament Mappings) | Settings → Filament inventory (pick provider, deduct toggle, low-stock), plus the provider's own page (Spoolman: Connection, Mappings tabs). Simple plugins: a section on Settings → Plugins | Medium (muscle memory). Old routes redirect |
| Three toggles that do nothing | One real "Deduct usage on job completion" toggle (default on, same as actual behavior today) | Positive. Release-note that the old "Deduct" toggle never worked |
| Spoolman down → pickers empty, preflight silent, deductions lost | Last-known spools shown as stale, deductions queued and replayed, alert after a user-set outage length | Positive, but new UI states to learn (stale badge, pending grams) |
| Inventory requires running Spoolman | Option: Local inventory, built in | Positive. New users get inventory with no extra service |
| Status chip "Spoolman ✓" | "Inventory: Spoolman ✓ / stale / ✗", hidden when no provider | Low |
| "pick…" from Spoolman | from the active provider | None |
| Scan spool parses Spoolman QR in-browser | Server `resolve-label`. Button shown only with `LABEL_SCAN` | None |
| Upgrade: nothing to do | Nothing to do | Must hold |
| Switching provider | Old links kept as "inactive"; pending deductions kept with apply/discard | New. Needs clear copy |
| External callers of `/api/v1/spoolman/*` | Work for one release (deprecated), then break | Medium. Release-note twice |

Unchanged: queue eligibility, slicing, printing, and costs (`job_costs.filament` stays manual).

---

## 6. Testing strategy

- **Provider contract suite**, parametrized over every `filament_inventory` plugin (Spoolman against
  `tests/spoolman_mock.py`, and Local inventory against the real test DB). It checks string refs, capability
  flags matching the implemented methods, `NotSupported` without a capability, DTO completeness, and
  `record_usage` idempotency for providers that claim it. A new provider has to pass it.
- **Host containment:** a provider that raises or hangs at each call site. Assert the request succeeds, the
  queue advances, and `state.last_error` is written. These tests must fail if the call wrapper is bypassed.
- **Queue-loop guard:** completion inserts an outbox row and makes no provider call inside the loop. Mock the
  provider and assert it isn't awaited on the loop's path.
- **Offline:**
  - provider down → reads come from the cache with `stale`, and effective remaining = cached − pending
  - recovery flushes rows in order and re-fetches
  - `max_disconnect_minutes` exceeded → exactly one `inventory.disconnected` (using `wait_until`, not sleep),
    then `inventory.reconnected`
  - ambiguous-timeout re-read marks the row applied rather than replaying it
  - restart while down still serves the cache
- **Dual-write normalizer:** one test per writer. That covers `PATCH /printers` (old key only, new key only,
  both, and a body that omits a slot) and an **AMS report preserving `inventory`**, which must fail against
  today's `printer_manager` merge. It also covers Laminus confirm and job/target/project/order writes with
  `filament_id` vs `material_ref`.
- **Migrations:** seed a v032 DB with a configured Spoolman, slot links, `filament_id` on every table
  including `orders.parts`, thresholds, the alerted set, and keys with `spoolman:*`, `settings:write`, and the
  admin session snapshot. Run v033+v034, then re-read every row. Also run them twice to check idempotency,
  plus `down()`, plus a fresh-DB `create_all` path. Plugin migrations go through the same idempotency and
  fresh-DB checks.
- **Compat shims:** golden responses captured from today's routes in Phase 0, compared byte-for-byte.
- **"No Spoolman in core" guard:** a grep test with an **explicit allowlist** that holds the shims,
  migrations, the normalizer, `models.SpoolmanConfig`, the `auth.py` scopes, and `printer_manager`'s
  preserved-keys set. It starts in Phase 1 and the allowlist shrinks in cleanup, so the guard runs the whole
  time instead of turning on at the end.
- **Frontend:**
  - `api/inventory.ts` via `stubFetch`
  - capability gating: no provider means no picker, chip, or scan button
  - stale and pending rendering
  - `PluginSettingsPage` (generated form, write-only secrets, pending-usage list)
  - section vs page nav contributions, tab routing, and the schema renderer
  - Local inventory screens
  - redirects
  - updated e2e `mock-api.ts`
- Regenerate `openapi.json`; update `contracts/response-keys.json` (including `project_item.filament_id`
  compat and the new keys) and `apiKeys.ts` SCOPES.

---

## 7. Phasing (if this moves past ideation)

Each phase is its own PR into `develop`, green at every step.

- **Phase 0 — safety net:** capture golden responses for the 8 Spoolman routes, `spool.low`, and
  `low_stock_warning`. Build the v032 fixture DB.
- **Phase 1 — host + Spoolman behind it, no visible change:** host, core tables, ABC/DTOs,
  `plugins/spoolman/`, core inventory services on DTOs, outbox + cache + disconnect alert, v033/v034, the
  normalizer on every writer, new routes, old routes as shims, and the guard with an allowlist. The frontend
  still uses the old routes. The outbox already fixes lost deductions here.
- **Phase 2a — frontend cutover:** `api/inventory.ts`, UI contributions + `PluginSettingsPage`, the core
  Filament inventory page, Spoolman's page + tabs, stale/pending UI, delete the dead toggles, `resolve-label`,
  and redirects.
- **Phase 2b — Local inventory:** plugin migrations, tables, CRUD routes, and screens. Adjust the ABC if it
  doesn't fit.
- **Phase 2c — cleanup (one release later):** guard the old migrations, then drop the shims, old tables,
  columns, keys, and scopes, and shrink the guard allowlist.
- **Later (separate design):** decide the install model, a second plugin kind, and an event bus.

---

## 8. Assumptions

1. One inventory system per farm (§3.4).
2. The queue keeps matching on type/color. A "specific material" ask stays informational, as today.
3. Profile links live in the provider. Both planned providers support them.
4. Upgrades need zero manual steps, with a one-release downgrade window.
5. External consumers of `/api/v1/spoolman/*` may exist (API keys, QR/home-automation), so the deprecation
   window is worth its cost.
6. The frontend stays one bundle for now. `component` tabs are in-tree.
7. Spoolman stays the recommended provider for people already running it. Local inventory is for everyone
   else, not a replacement push.
8. `job_costs.filament` stays manual. No pricing from providers.
9. While plugins are in-tree, in-process trust is acceptable.

## 9. Remaining open questions

- **Q1.** Install model (in-tree / pip / upload) and the sandbox posture that follows. Deferred while this is
  ideation.
- **Q6.** Compat window: one release or a fixed date?
- **Q7.** Ambiguous-timeout replay to Spoolman: accept the re-read heuristic, or have the user confirm
  ambiguous rows before replay?
- **Q8.** Local inventory nav: main sidebar ("Inventory", operational) as proposed, or under Settings?
- **Q9.** Is a one-shot "import from Spoolman" into Local inventory part of Phase 2b or a follow-up?

## 10. Docs to update when implemented

`CLAUDE.md` (a "Plugin host" key pattern; new tables; Spoolman table retirement), `docs/agent/backend.md`,
`data-model.md`, `frontend.md`, `backend-review.md` (§1 filament-id inventory: Spoolman refs vs AMS tray code
vs `material_ref`; new item: "core never branches on plugin id; provider calls go through `host.call`; queue
loop never awaits a provider"), `frontend-review.md` §2, and a new `docs/plugins.md` authoring guide parallel to
`docs/printer-interface.md`. Use `themis-docs-sync`.

## 11. Decision log

| # | Decision | Date |
|---|---|---|
| D1 | Ideation stage; install model not decided | 2026-10-03 |
| D2 | Delete "Push usage on job events" and "Mirror vendor & material catalog" toggles; make deduct-on-completion real | 2026-10-03 |
| D3 | Second provider is a built-in Local inventory with the minimum feature set for current Themis operation (§3.6) | 2026-10-03 |
| D4 | Provider down: serve the last-known spool list; accrue usage deltas until reconnection; alert after a user-set outage length in provider settings (§3.5) | 2026-10-03 |
| D5 | Plugins define their own settings UI: a shared default plugin page any plugin can use; simple plugins render as a section on a common page; complex ones get their own sidebar entry with tabs (§3.7) | 2026-10-03 |
