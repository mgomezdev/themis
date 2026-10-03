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

- Hot-loading plugins without a restart (§3.11 starts with restart-required).
- Custom frontend code from installed plugins (§3.11 starts with Themis-rendered `default`/`schema` tabs).
- Sandboxing untrusted plugins (approach B, §2).
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

The registry is a plain dict. It is filled at startup from bundled plugins and then from installed ones
(§3.11), both through the same `themis-plugin.toml` + `MANIFEST` format.

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
transaction, flushed later by the host's own task. Reads the queue triggers (the pre-print weight snapshot)
are scheduled on the host's task, and their result is written when it returns. The queue loop does no
external I/O at all, which is stricter than today's `create_task`.

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
    async def get_spool(self, spool_ref: str) -> InvSpool | None: ...  # required (pre-print snapshot)
    # Optional, gated by capability:
    async def set_remaining(self, spool_ref: str, remaining_g: float) -> None: ...   # WRITE_WEIGHT
    async def set_profile_links(self, material_ref: str, links: dict) -> InvMaterial: ...  # PROFILE_LINKS_WRITE
    def parse_label(self, text: str) -> str | None: ...         # LABEL_SCAN -> spool_ref
    def spool_url(self, ref: str) -> str | None: ...            # deep link into the provider's own UI
```

Capabilities: `TRACKS_WEIGHT`, `WRITE_WEIGHT`, `PROFILE_LINKS_READ`, `PROFILE_LINKS_WRITE`, `LABEL_SCAN`,
`REMOTE` (the provider lives elsewhere and can be unreachable; enables §3.5 offline behavior and the
disconnect-alert setting). **Core and UI branch only on capabilities, never on plugin id.**

**There is deliberately no "record usage / subtract N grams" method** (decision D6). Core never sends a delta.
It sends the absolute remaining weight it computed (§3.5), so a repeated send changes nothing. This is the
whole idempotency story, and it holds for every provider without needing provider-side dedupe. For Spoolman,
`set_remaining` maps to `PATCH /api/v1/spool/{id}` with `remaining_weight`. Before relying on it, verify
against the real API (e.g. that it isn't silently recomputed from `used_weight`) with a
`protocol_verification/`-style manual check.

### 3.3 Core vs. plugin responsibilities

| Concern | Lives in |
|---|---|
| Low-stock thresholds, `spool.low`, "alert once until refilled" | **Core** `services/inventory/alerts.py`, on *effective* remaining (§3.5). Needs `TRACKS_WEIGHT` |
| Preflight "not enough filament" warning | **Core**, on `InvSpool` effective remaining |
| Pre-print weight snapshot, post-print target weight, `deduct_on_complete` setting | **Core** (snapshot table + outbox, §3.5). Plugin `get_spool` / `set_remaining` do the I/O |
| Sync loop, health, last-known cache, outbox flush, disconnect alert | **Core** host, generic over `REMOTE` providers |
| Slot ↔ spool link, "specific material" job ask | **Core** data, with provider-namespaced refs (§4) |
| Orca profile links | Plugin (`PROFILE_LINKS_*`). Drift repair logic stays core and writes through `set_profile_links` |
| HTTP, auth, `extra` encoding, label format | **Plugin** |

Core inventory settings live in a new `inventory_config` singleton: `deduct_on_complete` (default on, which
matches actual behavior today), `low_stock_default_g`, `low_stock_overrides`, and `low_stock_alerted`.

### 3.4 One active provider (proposal)

`extension_slots.filament_inventory` holds 0 or 1 plugin. Refs are still stored with their provider id, so
switching is safe and a multi-provider future isn't blocked.

### 3.5 Deduction model and offline behavior (decided: snapshot + absolute set; last-known cache; alert)

#### Deduction: read at print start, set absolute weight at completion (D6)

Every deduction is an absolute "set this spool to N grams", computed from a weight snapshot taken when the
print starts:

1. **Print start** (the queue marks the job `printing`, the same moment `awaiting_plate_clear` is set): for
   each spool-linked slot the job uses, core records a row in `job_spool_snapshots`:

   | column | notes |
   |---|---|
   | `job_id`, `printer_id`, `slot` | |
   | `provider`, `spool_ref` | |
   | `pre_weight_g` | the spool's weight before this print |
   | `source` | `live`, `cached`, or `pending` (see below) |
   | `taken_at` | |

   - Taking the snapshot is a **read**, so it is scheduled off the queue loop like any provider call. The
     row is written as soon as it returns. It never delays the print start.
   - Where `pre_weight_g` comes from, in order:
     1. the newest *pending* target for that spool in the outbox (a previous job's set that hasn't
        reached the provider yet);
     2. otherwise a live `get_spool`;
     3. otherwise the last-known cache.
   - Option 1 is what makes back-to-back prints on one spool correct while the provider is offline.

2. **Completion** (`queue_engine` completion path and `complete-manually`): if `deduct_on_complete` is on and
   the provider has `WRITE_WEIGHT`, core computes `target_g = max(0, pre_weight_g − job_spent_g)`. It then
   inserts an outbox row `{provider, spool_ref, target_g, job_id}` **in the completion transaction**.
   - **Manual completion has no print start.** It takes the snapshot at completion time instead, from the
     same three sources, in the same order.
   - **A missing snapshot** (the provider was unreachable at start and nothing was cached) means: **skip,
     flag, notify, and suspend tracking for that spool until it is corrected** (D11). Guessing would be
     worse.
     - **Skip:** no outbox row.
     - **Flag:** the job shows "Filament usage not recorded — no starting weight for spool X".
     - **Suspend:** an `inventory_spool_status` row `{provider, spool_ref, tracking: "suspended", reason,
       since, job_id}`. While a spool is suspended, every later print on it is also skipped and flagged,
       because its recorded weight is known to be wrong and any base would be wrong too.
     - **Notify:** emit `inventory.tracking_unavailable` once per suspension (webhook + notification
       channels) with the message "Filament usage tracking is unavailable for spool X until its weight is
       corrected." The spool shows a persistent warning chip on its slot, in the pickers, and on the
       provider page.
     - **Correct:**
       - With `WRITE_WEIGHT`, the user enters the real weight in Themis, which calls `set_remaining`.
       - Otherwise the user fixes it in the provider and clicks **Weight corrected**.
       - Either way, Themis clears the suspension, re-fetches the spool, and emits
         `inventory.tracking_restored`.
     - Clearing is always an explicit user action. Themis can't tell on its own whether a changed weight is
       correct.

3. **Flush:** the host sends `set_remaining(spool_ref, target_g)` for each outbox row, in `created_at`
   order. It flushes right after commit (the normal online case, effectively immediate) and again on every
   reconnect.
   - **Re-sending a row is harmless.** The value is absolute, so a timeout or crash mid-flush just means
     sending it again. The double-deduction risk from earlier drafts is gone, and so is the re-read
     heuristic.
   - **When a send succeeds,** the row becomes `applied` and the host re-fetches that spool into the cache.

Outbox table `inventory_pending_writes`: `id`, `provider`, `spool_ref`, `target_g`, `job_id`, `printer_id`,
`source` (`queue`/`manual_complete`), `created_at`, `attempts`, `last_attempt_at`, `last_error`, and `status`
(`pending` → `applied`/`superseded`/`discarded`). When a newer row for the same spool is applied, older
`pending` rows for that spool become `superseded`, because only the latest absolute value matters.

**What absolute sets trade away: lost updates.** If someone changes the spool *in the provider* while a print
runs (re-weighs it, swaps it, or another tool deducts from it), the completion write overwrites that change
with `pre_weight − spent`. **MVP (D10): always overwrite**, because it is the simplest to implement.
Better behavior (hold and ask, auto-rebase, or warn only) is undecided and tracked in **BIZ-198**
(enhancement).

**Usage accounting today vs. this model:** today only `complete` deducts, which stays the same.
Failed and cancelled prints deduct nothing, so partial usage stays unrecorded as now. That could change
later, and the snapshot model makes it easy.

#### Offline behavior for `REMOTE` providers (D4)

- **Last-known cache.** After each successful `list_spools`/`list_materials`, the host persists the result to
  `inventory_cache(provider, kind, payload JSON, fetched_at)`, so it survives a restart. Reads (pickers,
  preflight, queue list, `/inventory/spools`) serve the live result when available and the cache otherwise,
  with `stale: bool` and `as_of`.
- **Effective remaining** = the newest pending target for the spool if there is one, else the cached or live
  weight. Preflight, low-stock alerts, and the pickers use effective remaining. The UI marks unsynced
  values ("412 g · not yet synced").
- **Pending writes accumulate** while the provider is down and flush in order on reconnect. Rows for a
  provider that is no longer active stay `pending`. The provider page lists them with **Apply when
  reconnected** (default) or **Discard**. They are never re-targeted at another provider.
- **Disconnect alert.** Each `REMOTE` provider's settings get the host-standard field `max_disconnect_minutes`
  (user-set, blank = never). The host tracks `state.disconnected_since`. When the limit is exceeded it emits
  `inventory.disconnected` (webhook + notification channels, same plumbing as `spool.low`) once per outage,
  with `{provider, since, pending_count}`. On recovery it emits `inventory.reconnected` with the flushed
  counts. The status chip turns red, and screens showing spool data get a banner.

### 3.6 Local inventory provider (decided: a plugin like any other; data + behavior wiring only)

Local inventory is **a plugin with no special status in core**. It goes through the same registry, manifest,
interface, capability flags, and settings storage as Spoolman. The only difference is that it stores data in
its own plugin-owned tables (§3.8) instead of relaying to a remote service. It requests its own page
(`ui.mode = "page"`), but **the page's screens, layout, and full feature set are out of scope** (D7). This
section only defines the data and behavior wiring needed to satisfy the interface for current Themis
operation.

| Interface obligation (from current Themis use) | Local inventory wiring |
|---|---|
| `list_materials` / `list_spools` / `get_spool` (pickers, preflight, low-stock, snapshot) | Read from plugin tables and map to DTOs |
| `TRACKS_WEIGHT` | `remaining_g` column |
| `WRITE_WEIGHT` → `set_remaining` | `UPDATE … SET remaining_g = :target` in a transaction, plus an append to its own audit table |
| `PROFILE_LINKS_READ/WRITE` (slot preset auto-pick, Laminus drift repair) | `profile_links` JSON on the material |
| `LABEL_SCAN` → `parse_label` | Its own code format, `themis:s-<id>` + bare id |
| Not `REMOTE` | No cache, disconnect alert, or offline outbox wait. Outbox rows flush immediately (same code path) |

**Tables** (plugin-owned, prefixed): `local_inv_materials(id, name, material, color_hex, vendor, density,
diameter, profile_links JSON, archived)`, `local_inv_spools(id, material_id FK, label, location, initial_g,
remaining_g, archived, created_at)`, and `local_inv_weight_log(id, spool_id, old_g, new_g, job_id NULL,
source, created_at)` (an audit trail for every set).

**Plugin routes** (data CRUD the future UI will need, mounted under `/api/v1/plugins/local_inventory/`): create,
update, and archive for materials and spools. These are listed so the routing wiring (plugin-owned routers,
§3.9) is proven by a real plugin. Their exact shape is decided with the UI.

**Out of scope:** UI screens, label printing, pricing, purchase history, import from Spoolman (D8).

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
- For `REMOTE` providers: `max_disconnect_minutes`, last sync, last error, cache age, and the pending-writes
  list with apply/discard. Spools with suspended tracking (D11) are listed with a correct-weight action.
- Danger zone: disable, and "remove plugin data" (asks for confirmation; only for plugin-owned tables).

**Core inventory page** (Settings → Filament inventory, core-owned): active provider picker (None, Spoolman,
Local inventory), `deduct_on_complete`, and the low-stock thresholds. It links to the active provider's own
page.

The sidebar shows a plugin's nav entry only while the plugin is enabled. The old routes `/settings/spoolman`
and `/settings/spoolman-mappings` redirect to the new ones.

Proposed layouts:
- **Spoolman** → `page`, settings placement, tabs: Connection (`default`) and Filament mappings
  (`component`, shared profile-links component).
- **Local inventory** → `page` (its own sidebar entry). Placement, tabs, and screens are out of scope (D7).
  For now it declares one `default` tab, which is enough to configure it.
- A trivial plugin → `section`.

The `component` renderer requires frontend code compiled into Themis, so it is available to **bundled**
plugins only. Installed plugins (§3.11) use `default` and `schema` tabs, which Themis renders itself. Remote
component loading comes later.

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
*      /api/v1/plugins/{id}/…                           # plugin's own routers (Spoolman relay, Local inventory CRUD)
PUT    /api/v1/extension-slots/filament_inventory       # {plugin_id | null}

GET    /api/v1/inventory/materials | /spools            # + stale, as_of
POST   /api/v1/inventory/sync-now
GET    /api/v1/inventory/sync-status                    # provider, health, disconnected_since, pending counts
GET    /api/v1/inventory/pending-writes ; POST …/{id}/discard ; POST …/{id}/resolve ; POST …/flush
PATCH  /api/v1/inventory/materials/{ref}/profile-links  # 409 without PROFILE_LINKS_WRITE
POST   /api/v1/inventory/resolve-label                  # replaces frontend parseSpoolCode
GET/PUT /api/v1/inventory/settings                      # deduct_on_complete + low-stock
```

**Scopes.** Plugin config routes are gated on the existing `settings:read/write`, so admins need no new
scope. Inventory routes use new `inventory:read/write`. The migration grants these to every key holding
`spoolman:*` **or** `settings:write` (precedent: v021). That includes the persisted admin session and device
keys, whose scopes are snapshots. The hand-mirrored `frontend/src/api/apiKeys.ts` SCOPES list is updated too.

**Spoolman calls are owned and relayed by the plugin (D9).** Core has no Spoolman routes and makes no
Spoolman calls.
- **Core logic** (preflight, low-stock, snapshot/deduction, pickers, drift repair) talks only to
  `host.active("filament_inventory")` through the interface. It doesn't know whether the data lives in
  Spoolman, Local inventory, Bambuddy, or anything else.
- **The Spoolman plugin owns everything Spoolman-specific:**
  - its HTTP client and config;
  - any Spoolman-only endpoints, mounted from its own router under `/api/v1/plugins/spoolman/…`. These
    relay to the configured Spoolman, e.g. a raw passthrough for spool/filament detail beyond the DTOs.
- **Old paths.** `/api/v1/spoolman/*` and `/api/v1/settings/spoolman*` move **into the Spoolman plugin's
  router** as deprecated aliases that relay as before (same response shapes).
  - They exist only while the Spoolman plugin is registered.
  - When it isn't the active provider, they return 409 with a pointer to `/inventory`.
  - When to remove them is the plugin's own versioning decision, not a core compat window.
  - Keys holding `spoolman:*` are accepted on those plugin routes.
- **Neutral payloads.** `spool.low` keeps its `spool_id` and `filament_id` keys (ints when the ref parses as
  one) and adds `provider`, `spool_ref`, and `material_ref`. `low_stock_warning` keeps its shape and adds
  `spool_ref`.

### 3.10 Feature gating by installed plugin kind and capability

Some behavior can't exist without a plugin of the required kind. The rule: **core asks `host.has(kind,
capability)`, and the frontend asks the same through `GET /api/v1/plugins` (exposed as a
`useCapability(kind, cap)` hook).** When a capability is missing, the feature is hidden in the UI and skipped
quietly in the backend, never an error to the user. Routes that need it return 409 with
`{"error": "capability_unavailable", "kind", "capability"}`.

| Feature | Requires | Without it |
|---|---|---|
| Spool picker on printer slots; "specific material" pick on jobs, projects, orders | `filament_inventory` (any) | Hidden. Manual type/color/name entry only (today's "Spoolman off") |
| Inventory status chip, sync-now, sync status | `filament_inventory` + `REMOTE` | Hidden. Local providers have nothing to sync |
| Disconnect alert, stale banners, pending-writes list | `REMOTE` | Hidden / never fires |
| Preflight "not enough filament" warning, low-stock alerts + thresholds UI, Fleet "g left" | `TRACKS_WEIGHT` | Warning absent (`low_stock_warning: null`). Thresholds page hidden. `spool.low` never fires |
| Pre-print snapshot, completion deduction, `deduct_on_complete` toggle | `TRACKS_WEIGHT` + `WRITE_WEIGHT` | No snapshot, no outbox row, toggle hidden. With weight read-only, the job still shows "used ~38 g" |
| Filament mappings tab / auto-pick Orca preset when a spool is loaded | `PROFILE_LINKS_READ` (+`_WRITE` to edit) | Tab hidden. Slot keeps a manually chosen preset |
| Laminus drift repair of profile links | `PROFILE_LINKS_WRITE` | That section of the drift report is omitted. Printer/job drift unaffected |
| Scan spool (Fleet) | `LABEL_SCAN` | Button hidden |
| Stored refs from a provider that is no longer active | n/a | Degraded "inactive" chip; never blocks printing (§4) |

The guard test (§6) asserts that no core code path calls a provider method without a capability check, and
that every row above has a "without it" test.

### 3.11 Plugin installation (decided: upload an archive, or install from a GitHub repo — D12)

Users install plugins from **Settings → Plugins** in two ways:
- **Upload** a `.zip`, `.tar.gz`, or `.tgz`.
- **Point at a GitHub repo**: URL, plus an optional ref (tag, branch, or commit) and an optional
  subdirectory for monorepos.

Both paths feed **one** install pipeline. The GitHub path just fetches the archive first.

**Package format.** The archive root (or the chosen subdirectory) contains:
```
themis-plugin.toml        # manifest metadata (below)
<python_package>/         # the plugin code; exports MANIFEST (PluginManifest, §3.1)
vendor/                   # optional: pure-Python deps the plugin needs beyond Themis's own
migrations/               # optional: plugin migrations (§3.8)
ui/                       # optional: prebuilt frontend bundle (see "Frontend" below)
README.md
```
```toml
id = "bambuddy_inventory"      # ^[a-z][a-z0-9_]{2,40}$, stable forever
name = "Bambuddy inventory"
version = "1.2.0"              # semver
kind = "filament_inventory"
host_api = 1                   # host refuses mismatches
entry = "bambuddy_inventory.plugin:MANIFEST"
min_themis = "2026.10"         # optional
publisher = "someone"          # display only, not verified
```
Bundled plugins (Spoolman, Local inventory) use **the same format**. They ship inside the image as source
`bundled`. They can be disabled but not uninstalled, and their ids are reserved: an upload claiming a bundled
id is rejected.

**Pipeline** (`plugins/installer.py`):
1. **Get the archive.**
   - Upload: a multipart stream to a temp file, with a size cap.
   - GitHub: resolve the ref to a commit SHA through the GitHub API, then download that commit's tarball
     (`codeload.github.com/<owner>/<repo>/tar.gz/<sha>`). **No `git` binary is needed in the image.**
   - Private repos: use a token stored as a secret on the install record.
2. **Extract safely** into `/data/plugins/.staging/<uuid>/`. Reject:
   - absolute paths and `..` segments (zip-slip)
   - symlinks, hard links, and device files
   - more than N files or more than M MB uncompressed (zip bombs)
3. **Validate** `themis-plugin.toml` against its schema: id format, reserved ids, `host_api` compatibility,
   `kind` known to this Themis, and the `entry` module present.
4. **Dry-run import in a subprocess** (`python -c "import …; check MANIFEST"`) with the plugin's `vendor/` on
   the path. Import errors, missing deps, and a manifest that doesn't match the toml all fail the install
   here, without ever touching the live process.
5. **Commit:** move to `/data/plugins/<id>/<version>/` and write an `installed_plugins` row. Keep the previous
   version directory for one-step rollback.
6. **Activate on restart** (MVP; see below). The UI shows "Restart Themis to finish installing".

`installed_plugins`: `plugin_id` PK, `version`, `source` (`bundled`/`upload`/`github`), `source_url`,
`ref`, `commit_sha`, `archive_sha256`, `installed_at`, `status` (`installed`/`active`/`error`/`pending_restart`/
`pending_removal`), `error`, `previous_version`.

**Startup loading.**
- **Bundled plugins first**, then each installed plugin. For each one: add its directory (and `vendor/`, placed
  **after** Themis's own site-packages so a plugin can't shadow Themis's libraries) to `sys.path`, import the
  `entry`, check that the toml and `MANIFEST` agree, run its migrations, register it, and mount its routers.
- **Any failure is contained.** An import error, a `host_api` mismatch, or a failed migration marks the
  plugin `error` with the message, and Themis boots anyway. Features that need it are then gated off (§3.10).
  A broken plugin must never stop Themis from starting.

**Restart, not hot-load (MVP, simplest first).**
- Python can't reliably unload modules, and FastAPI route tables and OpenAPI are built at startup. So install,
  upgrade, and uninstall all take effect on the next start.
- `POST /api/v1/system/restart` exits the process cleanly, and Docker's restart policy brings it back. The
  compose file must set `restart: unless-stopped`. On bare-metal dev, the user restarts it themselves.
- Hot-loading a *new* plugin is a later enhancement.

**Updates.**
- GitHub-sourced: "Check for updates" re-resolves the recorded ref, compares it to `commit_sha`, and shows
  the new version and commit before the user confirms.
- Uploaded: upload a newer version of the same id.
- Upgrades are never automatic.
- Downgrade = roll back to `previous_version`. Plugin migrations need `down()` for this to be safe, and the
  installer refuses a rollback across a migration that has no `down()`.

**Uninstall.** Disable, mark `pending_removal`, and the code directory is deleted on restart. **Plugin data is
kept by default**: its prefixed tables, its `plugin_configs` row, and pending writes. "Remove data" is a
separate, explicit choice. Core refs to that provider become "inactive" (§4).

**Frontend for installed plugins.** An installed plugin can't be compiled into Themis's bundle.
- **MVP:** uploaded and GitHub plugins get `default` and `schema` tabs only (§3.7). Both are rendered by
  Themis and need no plugin JS, so an inventory provider is fully usable without any frontend code. Only
  bundled plugins may use `component` tabs.
- **Later:** a plugin ships a prebuilt ES module in `ui/`. Themis serves it from
  `/plugin-assets/<id>/<version>/` and loads it with dynamic `import()`. A versioned plugin SDK
  (`window.__THEMIS_PLUGIN_SDK__`: React, the API client, design-system components) becomes a public contract
  under `host_api`. That is a real compatibility commitment, which is why it is deferred.

**Security posture: installing a plugin = trusting it fully.**
- A plugin runs as the Themis process (root in the container). It can read the DB, every secret and API key,
  the OrcaSlicer config, and the network, and it can command printers. There is **no sandbox**.
- Mitigations:
  - Install, upgrade, uninstall, and restart need an **interactive admin session**. API keys can't do it,
    even with `settings:write`, so a leaked automation key can't install code.
  - The install dialog shows the source (file name or repo + commit), the publisher string, the archive
    sha256, and a plain warning that the plugin gets full access.
  - The commit SHA is pinned, and there are no automatic updates.
  - Every install, upgrade, uninstall, and restart goes into the audit log.
  - Optional setting: "Allow plugin installation" (off disables the install UI and routes entirely).
  - Optional setting: an allowlist of GitHub owners.
- Running untrusted plugins safely would mean out-of-process plugins (approach B, §2). Out of scope.

**API** (admin session only):
```
POST   /api/v1/plugins/install                    # multipart archive
POST   /api/v1/plugins/install-from-github        # {repo_url, ref?, subdir?, token?}
GET    /api/v1/plugins/{id}/updates               # github-sourced only
POST   /api/v1/plugins/{id}/upgrade | /rollback
DELETE /api/v1/plugins/{id}?remove_data=false
POST   /api/v1/system/restart
```

---

## 4. Data migration

Core migrations go in this order: **add, backfill, dual-write, switch readers, then drop in a later
release**. Changing `INTEGER` to `TEXT` in place needs an SQLite table rebuild, and `job_printer_configs` has
the FK and partial-index history from v025/v031. All new migrations are idempotent and have a `down()` for the
downgrade window.

**v033_plugin_host**
1. Create `plugin_configs`, `extension_slots`, `plugin_schema_versions`, `inventory_config`,
   `inventory_cache`, `inventory_pending_writes`, `job_spool_snapshots`, `inventory_spool_status`, and
   `installed_plugins`. Jobs already `printing` at
   upgrade time get no snapshot. Their completion takes one at completion time, which is the same as the
   manual-completion path.
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
- Drop `spoolman_config`, the `filament_id` columns and keys, `spoolman_spool_id`, and the
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
- **Deductions survive outages and restarts, and can't double-count.** Today a deduction that fails while
  Spoolman is down is **lost** (log line only), and a retry of `PUT /use` would double-deduct. With the
  outbox plus absolute sets, a deduction is never lost and re-sending is a no-op.
- **Core logic doesn't care where inventory lives.** Spoolman, Local inventory, or a future Bambuddy plugin
  all look the same to it (D9).
- A second provider (Local inventory) proves the interface, and the host is reusable for the next plugin
  kind.

**Costs and risks**
- **Large diff.** About 83 files, 8 OpenAPI paths, new tables, and new events. It must be phased (§7).
- **Hot-table migrations + dual-write.** The normalizer must cover every writer, and missing one silently
  drops links. Tests per writer (§6).
- **Lost-update risk from absolute sets.** Any change made to a spool *in the provider* during a print gets
  overwritten at completion. The MVP accepts this; better handling is tracked in BIZ-198. Today's delta model doesn't
  have this problem, but it has double-count and lost-deduction problems instead.
- **The deduction depends on a starting weight.** If the provider is unreachable at print start and nothing
  is cached, the print is not deducted. The job flags it, but it is a hole that didn't exist before.
- **One more provider read per print start.** A read for each spool-linked slot. It runs off the queue loop
  and the snapshot is written when it returns, but it is still more traffic to Spoolman.
- **Spoolman `PATCH remaining_weight` behavior is an assumption.** It needs a manual verification check
  against a real instance before relying on it (§3.2).
- **Feature gating adds a test matrix.** Every row in §3.10 needs a "without it" test, in both backend and
  frontend.
- **Stale-data decisions.** Preflight and low-stock run on cached numbers during an outage. The UI must show
  `stale`/`as_of` wherever a number drives a decision.
- **Indirection tax.** Core → host → provider. The docs/agent set must describe the seam.
- **Interface shaped by n=2,** and both providers are ours. Still better than n=1.
- **Plugin-owned migrations** add a second migration track: ordering, downgrade, and "plugin removed from
  registry but its tables remain" all need handling.
- **Uploaded and GitHub plugins run arbitrary code with full trust (§3.11).**
  - This is the largest new risk in the design: a malicious or careless plugin can read every secret and
    command printers.
  - Mitigations are procedural (admin session only, pinned commit, warning, audit, kill switch), not
    technical.
- **Installed plugins are a new class of thing that can break Themis.**
  - Startup isolation (an error marks the plugin, never blocks boot) is a hard requirement, and it needs
    tests.
  - A Themis upgrade that bumps `host_api` disables every incompatible plugin until its author updates it.
- **Restart-to-apply.** Install, upgrade, and uninstall each need a Themis restart. That is fine under
  Docker's restart policy and briefly drops live printer connections. Schedule it for when nothing is
  printing, or warn when something is.
- **Dependency limits.** In the MVP, installed plugins can only use Themis's own libraries plus pure-Python
  code they vendor. Compiled deps (anything with C extensions) won't work until a pip-install-at-install
  step exists, which brings network and architecture problems.
- **`/data/plugins` becomes part of what users must back up.** It is already inside the `/data` volume.
- **The manifest + `host_api` become a public contract.** Every change to the ABC, DTOs, or manifest now
  needs a compatibility decision.
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
| Inventory systems: Spoolman or nothing | Settings → Plugins: upload a plugin archive or paste a GitHub repo. A restart applies it. A full-trust warning comes before install | New, and the most powerful change for users. The new risk sits here too |
| A Spoolman outage at print start goes unnoticed | That spool's tracking is suspended. The user is notified with a "correct weight" action | New warning state. Can be noisy if Spoolman is often down at print starts |
| External callers of `/api/v1/spoolman/*` | Work for one release (deprecated), then break | Medium. Release-note twice |

Unchanged: queue eligibility, slicing, printing, and costs (`job_costs.filament` stays manual).

---

## 6. Testing strategy

- **Provider contract suite**, parametrized over every `filament_inventory` plugin (Spoolman against
  `tests/spoolman_mock.py`, and Local inventory against the real test DB). It checks string refs, capability
  flags matching the implemented methods, `NotSupported` without a capability, DTO completeness, and
  `set_remaining` round-trip (set, then `get_spool` returns the same value; set twice = set once). A new
  provider has to pass it.
- **Host containment:** a provider that raises or hangs at each call site. Assert the request succeeds, the
  queue advances, and `state.last_error` is written. These tests must fail if the call wrapper is bypassed.
- **Queue-loop guard:** completion inserts an outbox row and makes no provider call inside the loop. Mock the
  provider and assert it isn't awaited on the loop's path.
- **Offline:**
  - provider down → reads come from the cache with `stale`, and effective remaining = newest pending target,
    else cached
  - recovery flushes rows in order and re-fetches
  - `max_disconnect_minutes` exceeded → exactly one `inventory.disconnected` (using `wait_until`, not sleep),
    then `inventory.reconnected`
- **Deduction model:**
  - The snapshot is taken at print start, from live, cached, or pending sources, in that priority order.
  - Completion writes `target = pre − spent`, clamped at 0.
  - Flushing the same row twice leaves one value.
  - Two back-to-back jobs on one spool while offline: job 2's snapshot is job 1's pending target, and the
    final value equals both deductions.
  - A newer applied row marks older pending rows `superseded`.
  - A missing snapshot means no write, and the job carries a flag.
  - Manual completion takes its snapshot at completion time.
  - Jobs printing at upgrade time are handled.
  - Conflict guard, if adopted: an outside change → `conflict`; current == target → `applied`.
- **Suspended tracking:** a missing snapshot creates exactly one suspension and one
  `inventory.tracking_unavailable`. Later prints on that spool are skipped and flagged. The correct-weight
  action clears the suspension, calls `set_remaining` when the provider supports it, and emits
  `inventory.tracking_restored`.
- **Installer:**
  - Rejects zip-slip, symlinks, oversize and too-many-files archives, bad tomls, reserved ids, `host_api`
    mismatches, and a failing dry-run import. A failure leaves nothing behind in `/data/plugins`.
  - Uploads accept zip, tar.gz, and tgz.
  - GitHub install resolves the ref to a SHA and downloads that exact SHA (stubbed HTTP).
  - Upgrade keeps the previous version; rollback refuses a migration without `down()`.
  - Uninstall keeps data unless asked.
  - Install routes reject API-key auth.
- **Startup isolation:** a plugin that raises on import, fails a migration, or mismatches `host_api` is
  marked `error` and Themis still boots. A fixture plugin is built as a real archive in the test.
- **Feature gating:** one test per §3.10 row with the capability absent (UI hidden, backend skips, route
  returns 409).
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
- **Spoolman plugin alias routes:** golden responses captured from today's routes in Phase 0, compared
  byte-for-byte against the plugin's relaying aliases.
- **"No Spoolman in core" guard:** a grep test with an **explicit allowlist** that holds
  migrations, the normalizer, `models.SpoolmanConfig`, the `auth.py` scopes, and `printer_manager`'s
  preserved-keys set. It starts in Phase 1 and the allowlist shrinks in cleanup, so the guard runs the whole
  time instead of turning on at the end.
- **Frontend:**
  - `api/inventory.ts` via `stubFetch`
  - capability gating: no provider means no picker, chip, or scan button
  - stale and pending rendering
  - `PluginSettingsPage` (generated form, write-only secrets, pending-writes list)
  - section vs page nav contributions, tab routing, and the schema renderer
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
  normalizer on every writer, new routes, old Spoolman routes moved into the plugin's router, and the guard
  with an allowlist. The snapshot + absolute-set deduction lands here. The frontend still uses the old
  routes.
- **Phase 2a — frontend cutover:** `api/inventory.ts`, UI contributions + `PluginSettingsPage`, the core
  Filament inventory page, Spoolman's page + tabs, stale/pending UI, delete the dead toggles, `resolve-label`,
  and redirects.
- **Phase 2b — Local inventory wiring:** plugin migrations, tables, provider implementation, CRUD routes,
  and a `default` settings tab only. Adjust the ABC if it doesn't fit. The UI/feature set gets a separate
  design.
- **Phase 2c — installation:** `installed_plugins`, installer pipeline (upload + GitHub), startup loader with
  isolation, restart endpoint, Settings → Plugins install UI, and admin-only gating. The bundled plugins
  are converted to the package format first, which proves the format on our own code before anyone
  else's.
- **Phase 2d — cleanup (one release later):** guard the old migrations, then drop the old core tables,
  columns, keys, and scopes, and shrink the guard allowlist. Removing the Spoolman plugin's deprecated alias
  routes is the plugin's own decision.
- **Later (separate design):** decide the install model, a second plugin kind, and an event bus.

---

## 8. Assumptions

1. One inventory system per farm (§3.4).
2. The queue keeps matching on type/color. A "specific material" ask stays informational, as today.
3. Profile links live in the provider. Both bundled providers support them.
4. Upgrades need zero manual steps, with a one-release downgrade window.
5. External consumers of `/api/v1/spoolman/*` may exist (API keys, QR/home-automation), so the Spoolman
   plugin keeps those paths as relaying aliases (§3.9).
6. Installed plugins get Themis-rendered UI only (`default`/`schema`) for now. `component` tabs are for
   bundled plugins.
7. Spoolman stays the recommended provider for people already running it. Local inventory is for everyone
   else, not a replacement push.
8. `job_costs.filament` stays manual. No pricing from providers.
9. The person installing a plugin is an admin who accepts that it runs with full trust. There is no
   sandbox.
10. Spoolman's `PATCH /api/v1/spool/{id}` accepts an absolute `remaining_weight` and keeps it. Unverified.
11. Only completed jobs deduct, as today. A spool-linked slot is used by one printer at a time.
12. Themis runs under a supervisor that restarts it after a clean exit (Docker `restart: unless-stopped`).
13. Outbound access to `api.github.com` / `codeload.github.com` is available for GitHub installs. Offline
    farms use upload.
14. Suspended tracking is per spool, not farm-wide.

## 9. Remaining open questions

- **Q12.** Should restart be automatic after install/upgrade/uninstall when nothing is printing, or always
  wait for the admin to click it?
- **Q13.** Should GitHub installs support private repos (a stored token) in the first cut, or public only?
- **Q14.** Is the optional "Allow plugin installation" kill switch on or off by default?
- Conflict handling for spools changed during a print → **BIZ-198**.

## 10. Docs to update when implemented

`CLAUDE.md` (a "Plugin host" key pattern; new tables; Spoolman table retirement), `docs/agent/backend.md`,
`data-model.md`, `frontend.md`, `backend-review.md` (§1 filament-id inventory: Spoolman refs vs AMS tray code
vs `material_ref`; new item: "core never branches on plugin id; provider calls go through `host.call`; queue
loop never awaits a provider"), `frontend-review.md` §2, and a new `docs/plugins.md` authoring guide parallel to
`docs/printer-interface.md`. Use `themis-docs-sync`.

## 11. Decision log

| # | Decision | Date |
|---|---|---|
| D1 | Ideation stage (install model later settled by D12) | 2026-10-03 |
| D2 | Delete "Push usage on job events" and "Mirror vendor & material catalog" toggles; make deduct-on-completion real | 2026-10-03 |
| D3 | Second provider is a built-in Local inventory with the minimum feature set for current Themis operation (§3.6) | 2026-10-03 |
| D4 | Provider down: serve the last-known spool list; accrue pending writes until reconnection; alert after a user-set outage length in provider settings (§3.5) | 2026-10-03 |
| D5 | Plugins define their own settings UI: a shared default plugin page any plugin can use; simple plugins render as a section on a common page; complex ones get their own sidebar entry with tabs (§3.7) | 2026-10-03 |
| D6 | Deduction is read + absolute set, not a delta: read the weight at print start, then at completion send `pre_weight − job_spent`. Re-sends have no effect (§3.5) | 2026-10-03 |
| D7 | Local inventory is an ordinary plugin requesting its own page. Its UI/feature set is out of scope; only data and behavior wiring is in scope (§3.6) | 2026-10-03 |
| D8 | No import into Local inventory for now | 2026-10-03 |
| D9 | Core never calls Spoolman. Spoolman routes and calls live in the Spoolman plugin, which relays to its configured instance. Core logic is provider-agnostic, and features that need a missing plugin kind or capability are hidden or disabled (§3.9, §3.10) | 2026-10-03 |
| D10 | Spool changed in the provider during a print: MVP always overwrites with `pre − spent` (simplest). Better behavior undecided; tracked in BIZ-198 (enhancement) | 2026-10-03 |
| D11 | Missing pre-print snapshot: skip the deduction, flag the job, notify the user, and suspend usage tracking for that spool until the user corrects its weight (§3.5) | 2026-10-03 |
| D12 | Plugins are installed from Settings → Plugins by uploading a zip/gzip archive or by pointing at a GitHub repo. Themis stores them under `/data/plugins` and registers their parts (§3.11) | 2026-10-03 |
