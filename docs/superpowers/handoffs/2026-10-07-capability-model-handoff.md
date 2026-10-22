# Handoff: replace plugin `kind` with a capability model

Feed this to a fresh session. It carries the agreed requirements, the decisions already made with the user, the proposed design,
and where the process stopped. Read the repo's `CLAUDE.md` first (concise replies; workflow; review gate).

## Where the process stopped

Brainstorming (superpowers:brainstorming), **architectural path**. Done: context exploration, 6 clarifying questions (all answered),
a sectioned design presented in chat. **Not done:** the user has *not yet approved the design* (their last message asked for this
handoff instead). Next steps, in order, and nothing before the approvals (HARD-GATE: no product code):

1. Ask the user to confirm/adjust the design below (reply "approved" or name a section to change).
2. Write the spec to `docs/superpowers/specs/2026-10-07-capability-model-design.md`, self-review it (placeholders, contradictions,
   scope, ambiguity), commit it, ask the user to review it.
3. After spec approval: invoke `writing-plans` (plan to `docs/superpowers/plans/`), user reviews, then implement in the main session
   (no per-task subagents), run real suites, then exactly one fresh non-fork reviewer, then PR into `develop`.

## The request (user's words, condensed)

Instead of plugins being a `kind` whose contract is the capability, plugins **provide a list of capabilities** that Themis registers
them as serving. Goals: plugins can build on each other via non-core capabilities; a large plugin can cover wider scope (e.g.
filament **and** customer management in one plugin). A settings page lists every known capability (core + discovered through plugin
registration) and lets the user choose which service provides each one when providers overlap.

## Decisions already made (user answered each)

| # | Question | Decision |
|---|---|---|
| 1 | What is a capability? | **A named service contract** (e.g. `inventory.filament`): interface + int version. Today's fine-grained flags (`WRITE_WEIGHT`, `REMOTE`, …) stay as *features* of a capability. One provider selected per capability. |
| 2 | Interface of plugin-defined capabilities | **Name + version, duck-typed**, optionally a list of required async method names verified at registration. Consumers call through the host (same timeout/containment). Definer documents the interface; may ship an ABC others import; Themis does not require one. |
| 3 | REST for capabilities | **Both**: provider routers always at `/api/v1/plugins/<id>/…`, and also at `/api/v1/capabilities/<cap>/…` dispatched to whichever plugin is active (409 `capability_unavailable` if none). |
| 4 | Plugin-to-plugin dependencies | **Declared `requires` and `optional`** (capability ids, optional min version). Unmet `requires` ⇒ plugin stays registered but "waiting on `<cap>`" and offers nothing. Optional resolved at call time. Cycles rejected. |
| 5 | Which plugin serves a capability by default | **Auto-select only the unambiguous case**: no stored choice + exactly one enabled provider ⇒ it is selected. A later provider never displaces a choice. Explicit "None" is remembered. Existing slot rows migrate as explicit choices. |
| 6 | Multi-capability plugin build/config | **One plugin = one settings model, one `factory`, one enabled flag, one instance.** `provides` maps each capability to the part of the instance that serves it (default: the instance; or a named attribute). Selection is per capability; enabled/config is per plugin. |
| 7 | Back-compat for `kind` / `host_api` | **None.** Prerelease. `host_api` **stays 1**; just change the manifest shape. No shim. Refactor the sample/bundled plugins to the new spec. |

## Proposed design (presented, awaiting approval)

### 1. Capability model
* Definition: `{id, version:int, label, description, required_methods?, features?}`.
* Core capabilities are defined in code under `backend/app/plugins/capabilities/`. `plugins/kinds/filament_inventory.py` moves there as
  `inventory.filament` (ABC, DTOs, features unchanged).
* Plugin-defined capabilities: manifest `defines`; id **must start with `<plugin_id>.`**; plugins cannot redefine core ids.
* Catalog = core defs ∪ defs of registered plugins. If a capability's only definer is uninstalled, it leaves the catalog and its stored
  selection is kept dormant.
* Registration checks a provider has the required async methods named in the definition.
* `provides: {cap_id: Provide(version, attr=None, features=frozenset(), routers=())}`; `features` is today's per-plugin `capabilities`.

### 2. Manifest and toml
* `kind` removed; `host_api` stays 1.
* Toml gains `provides`, `requires`, `optional` (lists of `cap` or `cap@N` min version) and `defines`; checked against `MANIFEST`
  exactly like `id/version/kind/host_api` are today (`plugins/package.py:check_matches`).

### 3. Host and selection
* `host.active(cap)`, `host.has(cap, feature)`, `host.call(cap, method, …)`, new `host.part(cap)` (the serving part of the instance).
  Containment/timeouts/redaction/state unchanged.
* Instances are per plugin: built when enabled **and** selected for ≥1 capability.
* New table `capability_selections(capability PK, plugin_id NULL, explicit BOOL)` replaces `extension_slots`. New migration (v040)
  maps `filament_inventory` → `inventory.filament`, copies existing rows as `explicit=1`, drops `extension_slots`.
* Selection rules: auto-select unambiguous; never displace; explicit None remembered.
* Unmet `requires` ⇒ "waiting on `<cap>`". A selection that would create a requirement cycle ⇒ 422.

### 4. REST
* Plugin-id routes unchanged. `Provide.routers` also served at `/api/v1/capabilities/<cap>/…` via a per-capability dispatcher to the
  active provider; 409 `capability_unavailable` when none, 404 if the provider lacks the path; each route keeps its own `require_scope`.
* **Risk / plan-time spike:** the dispatcher must keep FastAPI `dependency_overrides` working in tests. Prefer matching routes manually
  and invoking the matched route over mounting a sub-app.
* New: `GET /api/v1/capabilities` (definition, providers, selection, status, requirement edges);
  `PUT /api/v1/capabilities/{cap}/provider` (`{plugin_id|null}`). Remove `PUT /api/v1/extension-slots/{kind}`.

### 5. Settings UI
* New Settings → **Capabilities** page: every capability (core + plugin-defined) with a provider dropdown (incl. None), status chips
  (serving / waiting on X / no provider installed / dormant), dependency notes.
* Filament inventory page's provider picker moves to the new API. Frontend hooks `useActivePlugin(kind)` / `useCapability(kind, cap)` →
  `useCapabilityProvider(cap)` / `useFeature(cap, feature)`.

### 6. Refactor and docs
* Bundled `spoolman` and `local_inventory` → `provides={"inventory.filament": …}`.
* Docs sample (`docs/plugin development/sample/acme_inventory/`) refactored the same way; add a second capability to demonstrate
  `defines` and `requires`.
* Update `docs/plugin development/README.md`, `docs/plugins.md`, `docs/provider-interfaces.md`, `docs/agent/{backend,data-model,frontend}.md`.

### 7. Testing
Host selection/auto-select/dependency+cycle rules; migration mapping; duck-type validation at registration; dispatcher (409/404, scopes);
Capabilities page tests + existing e2e; `contracts/response-keys.json` and `openapi.json` (`python scripts/export_openapi.py` from repo
root) regenerated. Every behavior change needs a test that fails without it (repo rule).

### 8. Delivery
New feature branch off `develop` (see repo state). Plan phases: (1) registry+host, (2) migration+API, (3) dispatcher, (4) frontend,
(5) bundled plugins + sample + docs. One review pass at the end (`docs/agent/backend-review.md`, `frontend-review.md`).

## Where the code to change lives (found by grep; ~30 backend / ~25 frontend files touch kind/slots)

* Core plugin system: `backend/app/plugins/{__init__,manifest,host,package,loader,installer,migrations}.py`, `plugins/kinds/filament_inventory.py`,
  `plugins/spoolman/`, `plugins/local_inventory/` (each with `themis-plugin.toml`).
* Core consumers: `backend/app/services/inventory/*` (provider.py is the only accessor; also alerts, cache, deduction, preflight, read, sync),
  `backend/app/api/routes/{plugins,plugin_install,inventory,jobs,queue,laminus}.py`, `backend/app/main.py` (router mounting at import time, ~line 205-225),
  `backend/app/models.py` (`ExtensionSlot`), migrations `v034_plugin_host.py`, `v035_inventory_core.py` (next migration is v040).
* Frontend: `frontend/src/api/{plugins,inventory,files}.ts`, `components/{PluginSettingsPage,PluginInstallDialog,SearchModal}.tsx`,
  `screens/{PluginsPage,FilamentInventoryPage,NewJobScreen,FleetScreen,PrinterConsoleScreen,PrinterFilesScreen,ProjectBuilderScreen,MaterialMappingsPage}.tsx`
  (+ their tests, `test/inventoryFixtures.ts`).
* Tests to extend/update: `backend/tests/plugins/*` (esp. `dummy_plugin.py`, `pkg_builder.py` which embeds a `kind=` manifest,
  `test_filament_inventory_contract.py`), `backend/tests/api/test_plugins_api.py`, `tests/test_provider_boundary.py`, `tests/test_no_provider_in_core.py`.
* Existing behavior to preserve: active rule today = slot points at plugin AND `plugin_configs.enabled`; `set_slot` also enables;
  `host.call` never raises; secrets redacted; installed plugins may use only `default`/`schema` tabs and no `alias_routers`.

## Repo / environment state at handoff

* Working dir `C:\Users\mgome\Documents\projects\print-garden-apps\themis`, branch **`feature/fix-plugin-dryrun-windows-env`** (off `develop`), unpushed, no PR. Commits on it:
  * `267ac6d` fix(plugins): pass `SYSTEMROOT` to install dry-run subprocess on Windows (fixes 22 failing installer tests on Windows).
  * `8c855ad`, `3c314ab` sidebar nav scroll fixes (`frontend/dist` was rebuilt locally; dist is not the dev server).
  * `9894aa3` docs: `docs/plugin development/` guide + tested sample plugin.
* This handoff file is **uncommitted**. The capability work should start on a **new branch off `develop`** (`feature/capability-model`), not on the branch above.
  A PR for the branch above needs the review marker `.claude/review-state.json` (see `CLAUDE.md`, gate hook).
* A pre-existing unrelated stash exists (`stash@{0}` on `feat/camera-snapshot`); do not drop it.
* Backend dev server (PID 40468) is running on `100.119.238.68:8001` serving built `frontend/dist`; Vite dev is not running. Backend venv: `backend/.venv`.
* Baseline suites on `develop`: frontend 978 unit + 45 Playwright pass; backend 2383 pass (22 installer failures were the Windows bug, now fixed on the branch above).
* Shell gotcha: a long heredoc with embedded Python in the Bash tool failed once; use the Write tool for files.

## Unrelated open item

Another Claude session relayed a BIZ-202 manual verification request (sections A–E: upgrade/migration, real Spoolman, Local inventory,
plugin install, regression smoke). Only the automated baseline and the Windows installer fix were done. A–E manual items are not run;
B needs a live Spoolman. This capability refactor will change the surfaces that checklist covers (slots, plugin API), so re-run it after.
