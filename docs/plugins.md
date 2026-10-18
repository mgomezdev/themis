# Plugins: authoring and installing

A plugin is an in-process Python package that Themis loads at startup. It declares the **capabilities** it provides, requires
and defines (there is no plugin "kind"). A capability is a named, versioned service contract such as `inventory.filament` v1
(see `provider-interfaces.md` for that contract's ABC and feature flags); a capability declares how its providers are selected (`CapabilityDef.mode`, below) — for `inventory.filament` exactly one plugin serves it at a time.
Bundled plugins (`spoolman`, `local_inventory`) live in `backend/app/plugins/<id>/` and use the **same package format** as
installed ones. Design: Linear "Plugin architecture — design spec" (BIZ-202), §3.1 and §3.11.

## Package layout

```
themis-plugin.toml        # metadata (below); required, at the archive root (or the chosen subdirectory)
<python_package>/         # the code; its `entry` exports MANIFEST (a PluginManifest)
vendor/                   # optional: pure-Python dependencies beyond Themis's own
README.md
```

```toml
id = "acme_inventory"            # ^[a-z][a-z0-9_]{2,40}$ — stable forever; bundled ids (spoolman, local_inventory) are reserved
name = "Acme inventory"
version = "1.2.0"                # MAJOR.MINOR.PATCH[-pre]
host_api = 1                     # the host refuses any other value
provides = ["inventory.filament@1"]   # capabilities served, with the contract version (@N, default 1)
requires = []                    # capabilities that must be served for this plugin to work ("cap@N" = minimum version)
optional = []                    # capabilities used when present (resolved at call time)
defines  = []                    # capabilities this plugin introduces; ids must start "<plugin id>."  e.g. "acme_inventory.notes"
entry = "acme_inventory:MANIFEST"   # module:attribute
min_themis = "2026.10"           # optional, dotted-number compare with the running version
publisher = "Acme"               # optional; display only, NOT verified
# permissions = []               # reserved (BIZ-200): parsed, ignored
```

Unknown keys are rejected. The `MANIFEST` must agree with the toml on `id`, `version`, `host_api` and the four capability lists
(checked at install and at every start). The top-level package name must not already resolve in Themis (so a plugin cannot shadow `app`, the
standard library, a Themis dependency, or another plugin).

`PluginManifest` (see `app/plugins/manifest.py`): `settings_model` (pydantic; generates the settings form), `secret_fields`
(write-only), `factory(settings) -> instance` (one instance per plugin), `provides` (`{capability: Provide(version, attr, features,
routers)}`: `attr` names the part of the instance that serves it, default the instance itself; `features` are that capability's feature
flags), `requires` / `optional` (`Requirement(capability, min_version)`), `defines` (`CapabilityDef(id, version, label, description,
required_methods)`), `ui` (`UiContribution`: a `section` on Settings → Plugins or its own `page` with tabs), `routers` (mounted under
`/api/v1/plugins/<id>/…`), `migrations`, `table_prefix` (default `<id>_`).

### Providing filament inventory (`inventory.filament`)

There is no core capability named `library.filament`; the capability for filament materials and spools is **`inventory.filament` v1**. To claim it, declare it in both package metadata and the runtime manifest, and have the plugin factory return an implementation of `FilamentInventoryProvider`:

```toml
# themis-plugin.toml
provides = ["inventory.filament@1"]
```

```python
from app.plugins.capabilities.filament_inventory import (
    FilamentInventoryProvider, InvMaterial, InvSpool,
)
from app.plugins.capabilities.inventory_routes import shared_router
from app.plugins.manifest import HOST_API, PluginManifest, Provide

class MyInventory(FilamentInventoryProvider):
    # Advertise only optional behavior this implementation really supports.
    capabilities = frozenset()  # e.g. TRACKS_WEIGHT, WRITE_WEIGHT

    def __init__(self, settings):
        self.settings = settings

    async def test_connection(self) -> dict:
        ...

    async def list_materials(self) -> list[InvMaterial]:
        ...

    async def list_spools(self) -> list[InvSpool]:
        ...

    async def get_spool(self, spool_ref: str) -> InvSpool | None:
        ...

MANIFEST = PluginManifest(
    id="my_inventory",
    name="My Inventory",
    version="1.0.0",
    host_api=HOST_API,
    settings_model=MySettings,
    factory=MyInventory,
    provides={"inventory.filament": Provide(
        version=1,
        features=MyInventory.capabilities,
        routers=(shared_router,),
    )},
)
```

The four async methods above are the required provider surface. Return neutral `InvMaterial` / `InvSpool` DTOs and stable, provider-opaque string refs; `get_spool` returns `None` for an unknown spool. The `capabilities` class attribute must contain only supported feature flags, and `Provide.features` must expose the same set to core. Each optional flag adds methods and semantics: for example, `WRITE_WEIGHT` requires `set_remaining(spool_ref, remaining_g)` to set an **absolute** weight, and `MANAGE_MATERIALS` requires the material create/update/archive methods. The full flag-to-method map and DTO fields are in [provider interfaces](provider-interfaces.md#filament-inventory-provider-methods-and-http-routes). Run the provider contract suite when implementing a provider.

`Provide.routers` is how a provider opts into extra capability-dispatched HTTP routes. The `shared_router` example exposes low-stock settings at `/api/v1/capabilities/inventory.filament/low-stock`; it is not required for the provider methods to work. The core `/api/v1/inventory/*` API already dispatches to the active provider. A capability declaration alone does not create plugin HTTP routes. The active exclusive provider is selected in Settings → Capabilities. Integrations can inspect `GET /api/v1/capabilities` and should handle `409 capability_unavailable` when no provider is active or a requested feature is absent.

### Capabilities, selection and dependencies

* **One plugin = one settings model, one `factory`, one enabled flag, one instance.** Selection is per capability: a plugin that
  provides two capabilities can be selected for both, one, or neither.
* **Selection** (`capability_selections`, Settings → **Capabilities**, `PUT /api/v1/capabilities/{cap}/provider`): a plugin is
  *auto-selected* only when no choice is stored and exactly one enabled plugin provides the capability. A later provider never
  displaces a stored choice; an explicit "None" is remembered. Selecting a plugin also enables it.
* **`requires`**: until every required capability is served (at the minimum version) the plugin is registered but *waiting on
  `<cap>`* and offers nothing; it starts without a restart once the dependency is served. A selection that would create a
  requirement cycle is rejected (422). **`optional`** capabilities are checked at call time (`plugin_host.active(cap)`).
* **Plugin-defined capabilities** are duck-typed: a definition names an id, a version and, optionally, `required_methods`; the host
  verifies the serving part has those `async` methods (and that the provided version matches) when it builds the instance. Consumers
  call through `plugin_host.call(cap, method, …)` (same timeout and containment as core). A plugin cannot redefine a core id. If the
  only definer is uninstalled, the capability leaves the catalog and a stored selection for it is kept *dormant*.
* **REST**: routers listed in `Provide.routers` are served at `/api/v1/plugins/<id>/…` and also at `/api/v1/capabilities/<cap>/…`,
  dispatched to whichever plugin is currently active (409 `capability_unavailable` when none, 404 for a path the active plugin does
  not expose). Each route keeps its own `require_scope`. Routers in plain `routers` stay plugin-id-only.

### Events (BIZ-249)

A plugin may **define** event classes (`PluginManifest.defines_events=(EventDef("<id>.thing", version=1, durability="best_effort"|"durable",
payload_model=…),)`, names start `<id>.`) and **subscribe** to core or other plugins' events (`subscribes=(EventSubscription("job.complete",
"on_complete", timeout=10, queue_size=1000),)` with `async def on_complete(self, envelope)` on the instance). Neither goes in
`themis-plugin.toml`. A plugin with only events is still built when enabled; disabling it stops delivery at once, re-enabling
resumes it (no replay of best-effort events). Handlers are contained like any provider call, must be idempotent on `envelope.id`,
and their errors are redacted. Contract, delivery guarantees and operator endpoints: `docs/events.md`.

### Declarative UI (BIZ-303)

A plugin contributes UI without frontend code through two surfaces that share one contract version (`schema_version` 1,
`backend/app/plugins/ui_schema.py`, mirrored at runtime by `frontend/src/api/uiSchema.ts`):

* **Settings form**: generated from `settings_model`. Supported field kinds: boolean, number (`ge`/`le` become the input's
  min/max), text, list of text, and a list over a fixed string enum (checkboxes); an enum string is text. Secrets are always
  write-only password inputs. Anything else (a nested model, a dict, a list of numbers, a union of kinds) is *unsupported*: the
  page shows "Not editable here", never sends that field, and the plugin carries a `settings.<field>` diagnostic.
* **`schema` tabs**: the document `ui_schema(tab_id)` returns. It is evaluated **once, when the plugin registers**.

```json
{"schema_version": 1, "title": "Library", "blocks": [
  {"type": "form", "title": "Defaults", "load": "defaults", "save": "defaults", "fields": [
    {"key": "currency", "label": "Currency", "type": "text", "required": true, "max_length": 3},
    {"key": "spool_g", "label": "Spool size (g)", "type": "number", "min": 1, "max": 10000},
    {"key": "unit", "label": "Unit", "type": "select", "options": [{"value": "g", "label": "grams"}, {"value": "kg", "label": "kilograms"}]},
    {"key": "kg_note", "label": "Note", "type": "textarea", "visible_if": {"key": "unit", "equals": "kg"}}]},
  {"type": "table", "title": "Materials", "data": "materials", "empty": "No materials yet.",
   "columns": [{"key": "name", "label": "Name"}, {"key": "material", "label": "Type"}],
   "create": {"path": "materials", "label": "Add material", "fields": [{"key": "name", "label": "Name", "type": "text", "required": true}]},
   "row_actions": [{"label": "Archive", "method": "POST", "path": "materials/{id}/archive", "confirm": "Archive it?"}]}]}
```

Field types: `text`, `number`, `textarea`, `checkbox`, `select` (needs `options`). Constraints: `min`/`max` (number),
`max_length` (text/textarea), `required`, `visible_if` (`{key, equals}` naming *another* field of the same form: the field shows only
while that sibling holds the value, and a hidden field is left out of what is saved). Every `load`/`save`/`data`/`path` is
relative to `/api/v1/plugins/<id>/` and may use `{row_key}` placeholders; absolute URLs, a leading `/`, a `:` and `..` are rejected.
The same routes validate what is submitted: the contract only describes the form, the plugin's own route is the authority.

**Validation and failure behaviour**

| Situation | Backend | Frontend |
|---|---|---|
| Unknown `schema_version`, block type, field type, extra key, bad path or constraint | The tab is dropped from `ui.tabs`, a `ui.tab.<id>` diagnostic names the path and reason, `GET /plugins/<id>/ui/<tab>` answers 422; the plugin and every other plugin keep running | The tab shows "This tab cannot be displayed: ‹reason›" and nothing else on the page is affected |
| `ui_schema` raises, or returns nothing for a schema tab | Same; the diagnostic names the exception *type* only (its message may carry a secret) | n/a |
| Settings field outside the contract | `settings.<field>` diagnostic | "Not editable here"; not sent on save |
| Unknown `settings_schema_version` | n/a (the backend only emits 1) | The settings form and secret inputs are not rendered; the enable switch still works |
| Submitted setting key the model does not declare | 422, nothing saved | n/a |
| Invalid value / constraint violated | 422 naming the *fields*, never the value | The message is shown |

Diagnostics appear in `GET /plugins` and `GET /plugins/<id>` (`diagnostics`), and on the plugin's settings page.

**When a contribution exists** (`navigablePlugins`, `PluginPage`): a plugin's navigation entry, tabs and settings section exist only
while the plugin is **installed and running** (`loaded`: not staged for removal, not failed to load) and **enabled**. Disabling hides
them at once; uninstalling drops them at the restart that applies it; a bundled plugin that is disabled shows only an "Enable"
prompt. A *transient provider error* (for example the server is unreachable) does not hide the page: it is where the admin fixes
the setting. A `component` tab additionally needs the capability feature it names (`requires`), so switching the provider of a
capability adds or removes those tabs on the next refresh. When every declared tab is dropped, the plugin falls back to its default
settings page.

**Custom components.** `component` tabs (compiled-in React, bundled plugins only) are for UI that cannot be described by the
blocks above. They are not versioned by this contract: they are part of the build and keyed in `frontend/src/plugins/registry.ts`,
so an installed plugin cannot use one and a missing key renders an explicit "component not part of this build" notice.

Data ownership, retention and recovery on disable / uninstall / reinstall / upgrade / rollback: [plugin-lifecycle.md](plugin-lifecycle.md).

### What an installed plugin may and may not do

* UI: **`default` and `schema` tabs only.** `default` is the generated settings page; `schema` is a form/table Themis renders
  from JSON your plugin serves at `GET /api/v1/plugins/<id>/ui/<tab>` (`ui_schema`). `component` tabs (React compiled into
  Themis) are for bundled plugins. A manifest with one fails to load.
* `alias_routers` (routes at absolute paths) are bundled-only: they could shadow core routes.
* Migrations: `down()` is required. Plugin tables must start with the table prefix. Migrations run in a savepoint; a failure marks
  the plugin `error` and never stops Themis.
* A provider's methods are `async`; wrap blocking SDKs with `run_in_executor`. Core calls you through `host.call` (timeout,
  exceptions contained, state recorded) — never raise into core on purpose, but nothing breaks if you do.

### Configuration and defaults (BIZ-289)

A plugin **never reads the process environment** and adds no plugin-specific env vars (`tests/test_plugins_do_not_read_environment.py`
fails on `os.environ`/`getenv` anywhere under `app/plugins/` except the core installer's pip-subprocess env, which is host code, and
under `app/services/providers/laminus/`). Anything the environment decides reaches the plugin through core:

* **`defaults.toml`** next to `themis-plugin.toml` (bundled: packaged via `package-data`; installed: shipped in the zip). Optional.
  ```toml
  enabled = false              # replaces the manifest's default_enabled
  [settings]                   # shipped defaults for your settings model
  url = "http://orca:5000"
  [legacy_env]                 # DEPRECATED, BUNDLED PLUGINS ONLY (ignored for installed ones): setting (or "enabled") -> old env var
  url = "LAMINUS_SIDECAR_URL"
  ```
* **Setting precedence** (host-resolved in `PluginHost.effective_settings`; blank saved values count as unset):
  saved setting (Settings page) > core override input (a `[legacy_env]` variable, read by core through `config.read_env`, deprecation
  warning logged once per process) > `defaults.toml [settings]` > the settings model's field default. Secrets are saved-only.
  The plugin only sees the merged result in its settings model. `enabled` (when no saved row exists): `[legacy_env] enabled` >
  `defaults.toml enabled` > manifest `default_enabled`; a saved enabled state always wins.
* **`PluginContext`** (`app/plugins/context.py`) carries core-defined values: `plugin_id`, `data_dir` (`<THEMIS_DATA_DIR>/plugin-data/<id>`,
  not created for you) and `plugin_dir`. A factory opts in with a second parameter: `factory(settings, context)`; `factory(settings)`
  keeps working. Add new core-defined values to the context, not to the plugin.
* Compose/Dockerfile carry only infrastructure Themis itself needs (volumes, ports, the `orca` service), never plugin config.

Bundled examples: `laminus` (`url` default = the compose `orca` service, plus `health_timeout_s` / `request_timeout_s` / `poll_timeout_s` connection tuning in its `defaults.toml`; `UiContribution.connection_status` shows a persistent unconfigured/unavailable/healthy pill on its page; `LAMINUS_SIDECAR_URL` deprecated) and `mock` (off by default;
`THEMIS_MOCK_PRINTERS` deprecated).

## Installing

Settings → Plugins → **Install plugin**: upload a `.zip` / `.tar.gz` / `.tgz`, or give a **public GitHub repository** (URL, optional
ref — tag, branch or commit — and optional subdirectory for monorepos). The dialog shows the source, publisher (unverified), the
archive's SHA-256 and a full-trust warning; you confirm explicitly. **No code from the package runs until you confirm**
(previews and "Check for updates" only extract and read the toml). Nothing is active until you **restart**.

Pipeline (`app/plugins/installer.py`), the same for both sources:

1. **Fetch.** Upload streams to disk under a size cap (25 MB). GitHub: the ref is resolved to a commit SHA through the API and that
   commit's tarball is downloaded from codeload — the moving ref is never installed. No `git` binary involved.
2. **Safe extract** into `<data>/plugins/.staging/<token>/`: absolute paths, `..`, backslashes, symlinks, hard links, device files
   and encrypted entries are rejected; at most 3000 files and 80 MB expanded (counted as bytes are written, so a lying header
   cannot bypass it). Tar decompression is capped too (headers and directory entries are not file bytes), and every entry
   counts toward the file limit. The upload route refuses an oversized or length-less body (413) before it is read; staged
   previews that are never confirmed expire after an hour.
3. **Validate** the toml (id format, reserved ids, semver, `host_api`, capability lists, entry shape and file, `min_themis`, name clash).
4. **On confirmation — dry-run import in a subprocess** (clean environment, throwaway data dir, `vendor/` after Themis's own
   path): import errors, missing dependencies and a MANIFEST that disagrees with the toml fail here, without touching the live
   process. This is the first time any of the package's code runs, and only after the admin has accepted the trust warning.
5. **Commit** (one at a time): move to `<data>/plugins/<id>/<version>/`, write the `installed_plugins` row (`pending_restart`),
   audit-log it. A failure at any step removes the staging directory and anything moved, and leaves no row.

Installing a **different version of an installed id** is an upgrade: the version that is *running* stays on disk as
`previous_version` (older ones are pruned). Upgrading again before a restart keeps that running version, not the staged one that
never ran. Re-installing the *same* version is refused — bump `version`.

### Restart (always the admin's call)

Install, upgrade, rollback and uninstall only **stage** their change (disk + `installed_plugins`). Changes stack; the Plugins page
shows one banner — "Restart Themis to apply N pending changes" — and one **Restart** button, which warns when a printer is
printing. `POST /api/v1/system/restart` exits the process cleanly; Docker's `restart: unless-stopped` (set in
`docker-compose.yml`) brings it back; the page waits for Themis to drop and return, then reloads. On a bare-metal dev run, restart it yourself. Because staging is durable, a crash or an
unrelated restart applies the same batch.

### Startup loading (`app/plugins/loader.py`)

Bundled plugins first, then each installed one (reads `installed_plugins` synchronously — the async engine is not up yet): its
directory and `vendor/` are **appended** to `sys.path` (after Themis's own packages), the entry is imported, toml and MANIFEST
are compared, the manifest is registered and its routers mounted. **Any failure is contained**: that plugin is recorded as
`error` with the message (shown on its row) and Themis boots anyway; features needing it are gated off as for any missing
provider. After migrations, each row becomes `active` or `error`; `pending_removal` code is deleted.

### Update, rollback, uninstall

* **Check for updates** (GitHub-installed only): re-resolves the recorded ref and compares the commit with the stored SHA; the
  upgrade review shows the new version/commit before you confirm. Never automatic. Uploaded plugins: upload a newer version.
* **Roll back** swaps to `previous_version` at the next restart. **Refused** when the database already holds plugin migrations that
  the previous version does not ship (it would run against a newer schema); upgrade forward instead.
* **Uninstall** disables the plugin now, marks `pending_removal`, and deletes the code at the restart. Its data (prefixed tables,
  settings, queued writes) is **kept** unless you tick "also delete this plugin's data" (`?remove_data=true`: runs its migrations'
  `down()`, newest first, and drops its settings row and capability selections, in one transaction with the audit row; refused when the
  plugin is not loaded, since its migrations are then unavailable). Bundled plugins can be disabled, never uninstalled.

### API (admin session only)

```
POST   /api/v1/plugins/install[?preview=true]      multipart `file`
POST   /api/v1/plugins/install-from-github         {repo_url, ref?, subdir?, preview?}
POST   /api/v1/plugins/install/{token}/commit      commit a previewed package      DELETE /plugins/install/{token}  discard it
GET    /api/v1/plugins/{id}/updates                github-sourced only
POST   /api/v1/plugins/{id}/upgrade[ {preview} ]   |  POST /plugins/{id}/rollback
DELETE /api/v1/plugins/{id}?remove_data=false
GET    /api/v1/system/restart                      pending changes + printers currently printing
POST   /api/v1/system/restart                      {force?}; 409 {error:"printing", printers} unless force
```

## Security posture

**Installing a plugin means trusting it fully.** It runs as the Themis process (root in the container): it can read the database,
every secret and API key, the OrcaSlicer config, the network, and command printers. There is no sandbox, and the dry-run
subprocess (run only after you confirm) only protects the live process from import-time crashes, not from malicious code. Mitigations that are in place:

* Every action above needs an **interactive admin**: the keyless local admin, the bootstrap key, or an admin login session. An API
  key — even with `settings:write` — gets 403, so a leaked automation key cannot install code (`auth.require_admin_session`).
* Pinned commit SHAs (GitHub), the archive SHA-256 shown before confirming, no automatic updates.
* Every install, upgrade, rollback, uninstall and restart is written to the append-only `audit_log` table (actor, action, target,
  detail incl. source and hashes). There is no UI for it yet.
* Installation is always allowed — there is no kill switch or allowlist (decision D15).

Out of scope (tracked): private GitHub repos (BIZ-199), declared permissions (BIZ-200), hot-loading, remote frontend components.

## Capability paths (replaceable providers)

`/api/v1/capabilities/<capability>/…` is the provider-independent dispatch path for routes listed in the selected provider's
`Provide.routers`. For filament inventory, the bundled providers include the shared `low-stock` GET/PUT routes
(`app/plugins/capabilities/inventory_routes.py:shared_router`), but third-party providers must opt in by listing that router.
Provider-specific routes such as Local inventory's `weight-log` are optional and answer 404 when the active provider does not expose
them; no selected provider answers 409. A plugin route `PUT /provider` is never exposed there (the selection route owns it). Common
functionality lives in the neutral core API (`/api/v1/inventory/*`); the old `/api/v1/spoolman/*` paths are deprecated aliases.

## Capability resolution modes (BIZ-250)

`CapabilityDef.mode` states the cardinality and dispatch policy, enforced by the host (`tests/plugins/test_capability_modes.py`):

| Mode | Providers | Dispatch | Example |
|---|---|---|---|
| `exclusive` (default) | exactly one active; selecting another replaces it | `host.call(cap, ...)` / `host.part(cap)` | `inventory.filament` |
| `routed` | every enabled plugin | the provider bound to the resource: `host.call_for(HANDLE, plugin_id, fn)` | `printer.client` |
| `choose_one` | every enabled plugin built; the selection row is the **default** | `host.resolve(cap, preferred=, eligible=)` / `call_choose`: per-resource preference → default → blocked. Eligibility is checked first; a dormant (disabled/removed) or ineligible preference **blocks**, never falls back to another implementation | slicing (`gcode.produce`: each printer's `preferred_slicer_plugin_id`, resolved by `slice_resolution.resolve_slicing`, BIZ-291) |
| `fan_out` | every enabled plugin | `host.fan_out(HANDLE, fn)`: each provider is called independently (own timeout/containment), one failing never blocks the rest | notifications (`notify.channel`, BIZ-252) |

Disabling or uninstalling a provider keeps resource references (printer bindings, preferences) as dormant data and surfaces the
state; the host never silently selects another implementation. Printer models are core-owned (`printer_models`, BIZ-262): plugins
contribute `Manufacturer`/`PrinterModel` declarations (optionally `equivalents=` for G-code compatibility, BIZ-263) and Themis assigns
the stable UUIDs.

### Notification channels (`notify.channel`, BIZ-252)

A **fan-out** capability: every enabled provider receives every human-facing notification, independently. Bundled providers: `notify_ntfy`,
`notify_discord`, `notify_email` (default-enabled; each has its own Settings page and an `events` allow-list, empty = sends nothing).
To add a channel, write a plugin that provides `notify.channel@1` with a settings model that subclasses `ChannelSettings` (it gets the
`events` checkboxes) and a factory returning a `FilteredChannel` subclass: implement `configured()` and `async send(message)` (raise on
failure). Core never changes. `deliver(ChannelMessage)` returns `ChannelResult` (`skipped` = not for this channel / not configured);
`test_connection()` powers the page's **Test connection** button. Secrets belong in `secret_fields` (write-only; errors are redacted).
A channel failure shows as the plugin's `last_error`; `ChannelMessage.message_id` is stable per notification if a channel retries.
Webhooks for companion apps are a separate mechanism (`docs/companion-apps.md`).
