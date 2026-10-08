# Capability model: replace plugin `kind` with provided capabilities

Date: 2026-10-07. Status: draft for review. Handoff: `docs/superpowers/handoffs/2026-10-07-capability-model-handoff.md`.

## Goal

Plugins stop being a `kind` whose contract is the capability. A plugin instead **provides a list of capabilities** that Themis
registers it as serving. This lets plugins build on each other through non-core capabilities, and lets one large plugin cover a
wider scope (e.g. filament inventory and customer management). A settings page lists every known capability (core + plugin-defined)
and lets the user pick which plugin serves each one when providers overlap.

## Decisions (agreed with user)

| # | Topic | Decision |
|---|---|---|
| 1 | Capability | A named service contract (e.g. `inventory.filament`): interface + integer version. Existing flags (`WRITE_WEIGHT`, `REMOTE`, ...) remain **features** of a capability. One provider is selected per capability. |
| 2 | Plugin-defined interface | Name + version, duck-typed, with an optional list of required async method names verified at registration. Consumers call through the host (same timeout/containment). The definer documents the interface and may ship an ABC; Themis does not require one. |
| 3 | REST | Provider routers always at `/api/v1/plugins/<id>/...`, and also at `/api/v1/capabilities/<cap>/...` dispatched to the active plugin (409 `capability_unavailable` if none). |
| 4 | Dependencies | Manifest declares `requires` and `optional` (capability id, optional min version). Unmet `requires` => plugin stays registered, shown "waiting on `<cap>`", offers nothing. `optional` is resolved at call time. Requirement cycles are rejected. |
| 5 | Default selection | Auto-select only the unambiguous case: no stored choice and exactly one enabled provider. A later provider never displaces a choice. Explicit "None" is remembered. Existing slot rows migrate as explicit choices. |
| 6 | Multi-capability plugin | One plugin = one settings model, one `factory`, one enabled flag, one instance. `provides` maps each capability to the part of the instance serving it (default: the instance; or a named attribute). Selection is per capability; enabled/config is per plugin. |
| 7 | Compatibility | None (prerelease). `host_api` stays **1**; only the manifest shape changes. No shim. Bundled plugins and the docs sample are refactored. |

## Design

### 1. Capability model
- Definition: `{id, version: int, label, description, required_methods?, features?}`.
- Core capabilities are defined in code under `backend/app/plugins/capabilities/`. `plugins/kinds/filament_inventory.py` moves there as
  `inventory.filament` (ABC, DTOs, features unchanged).
- Plugin-defined capabilities come from manifest `defines`. The id **must start with `<plugin_id>.`**; plugins cannot redefine core ids.
- Catalog = core definitions plus definitions of registered plugins. If a capability's only definer is uninstalled it leaves the catalog and
  its stored selection is kept dormant (restored if the definer returns).
- Registration verifies the provider part exposes the required async methods named in the definition; failure rejects that provide
  entry with a recorded reason.
- `provides: {cap_id: Provide(version, attr=None, features=frozenset(), routers=())}`. `features` is today's per-plugin `capabilities` set.

### 2. Manifest and toml
- `kind` is removed; `host_api` stays 1.
- `themis-plugin.toml` gains `provides`, `requires`, `optional` (lists of `cap` or `cap@N`, N = minimum version) and `defines`. They are
  checked against `MANIFEST` exactly as `id/version/kind/host_api` are today (`plugins/package.py:check_matches`).

### 3. Host and selection
- API: `host.active(cap)`, `host.has(cap, feature)`, `host.call(cap, method, ...)`, new `host.part(cap)` (the serving part of the instance).
  Containment, timeouts, redaction and state handling are unchanged (`host.call` never raises).
- Instances are per plugin, built when the plugin is enabled **and** selected for at least one capability.
- New table `capability_selections(capability PK, plugin_id NULL, explicit BOOL)` replaces `extension_slots`. Migration `v040`: map
  `filament_inventory` -> `inventory.filament`, copy existing rows with `explicit=1`, drop `extension_slots`.
- Selection rules: auto-select the unambiguous case; never displace a choice; explicit None is remembered. Active rule keeps today's
  meaning: selection points at the plugin AND `plugin_configs.enabled`; selecting also enables.
- Unmet `requires` => "waiting on `<cap>`". Selecting a provider that creates a requirement cycle => 422.
- Installed plugins keep today's limits (only `default`/`schema` tabs, no `alias_routers`).

### 4. REST
- Plugin-id routes are unchanged. `Provide.routers` are also served at `/api/v1/capabilities/<cap>/...` through a per-capability dispatcher
  to the active provider: 409 `capability_unavailable` when none, 404 if the provider lacks the path; each route keeps its own
  `require_scope`.
- Risk (resolve in the plan, spike first): the dispatcher must keep FastAPI `dependency_overrides` working in tests. Prefer matching routes
  manually and invoking the matched route over mounting a sub-app.
- New `GET /api/v1/capabilities` (definition, providers, selection, status, requirement edges) and
  `PUT /api/v1/capabilities/{cap}/provider` (`{plugin_id|null}`). `PUT /api/v1/extension-slots/{kind}` is removed.

### 5. Settings UI
- New Settings -> **Capabilities** page: every capability (core + plugin-defined) with a provider dropdown (including None), status chips
  (serving / waiting on X / no provider installed / dormant) and dependency notes.
- The Filament inventory page's provider picker moves to the new API. Hooks `useActivePlugin(kind)` / `useCapability(kind, cap)` become
  `useCapabilityProvider(cap)` / `useFeature(cap, feature)`.

### 6. Refactor and docs
- Bundled `spoolman` and `local_inventory` become `provides={"inventory.filament": ...}`.
- The docs guide and sample plugin (`docs/plugin development/`) live on an unmerged branch and are out of scope here; they get
  refactored to capabilities when that branch lands.
- Update `docs/plugins.md`, `docs/provider-interfaces.md`, `docs/agent/{backend,data-model,frontend}.md`.

### 7. Testing
Every behavior change gets a test that fails without it. Cover: host selection, auto-select, dependency and cycle rules; the v040 migration
mapping; duck-type validation at registration; the dispatcher (409/404, scopes); Capabilities page tests and existing e2e. Regenerate
`contracts/response-keys.json` and `openapi.json` (`python scripts/export_openapi.py` from the repo root). Update `dummy_plugin.py`,
`pkg_builder.py`, `test_filament_inventory_contract.py`, `test_provider_boundary.py`, `test_no_provider_in_core.py`.

### 8. Delivery
Branch `feature/capability-model` off `develop`. Plan phases: (1) registry + host, (2) migration + API, (3) dispatcher, (4) frontend,
(5) bundled plugins + sample + docs. Implement in the main session, run backend and frontend suites, then one fresh non-fork review pass
(`docs/agent/backend-review.md`, `frontend-review.md`), then PR into `develop`.

## Scope touched (from grep)
Backend: `app/plugins/{__init__,manifest,host,package,loader,installer,migrations}.py`, `plugins/kinds/`, `plugins/spoolman/`,
`plugins/local_inventory/`, `app/services/inventory/*`, `api/routes/{plugins,plugin_install,inventory,jobs,queue,laminus}.py`, `main.py`
router mounting, `models.py` (`ExtensionSlot`), migrations v034/v035. Frontend: `api/{plugins,inventory,files}.ts`,
`PluginSettingsPage`, `PluginInstallDialog`, `SearchModal`, and the plugin/inventory/fleet/job screens with their tests.

## Out of scope
- Backward compatibility with `kind` manifests or `host_api` bumps.
- Multiple simultaneous providers for one capability.
- The plugin development guide + sample (not on `develop`; update when that branch lands).
- The BIZ-202 manual verification checklist (re-run after this lands).
