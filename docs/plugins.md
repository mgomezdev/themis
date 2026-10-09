# Plugins: authoring and installing

A plugin is an in-process Python package that Themis loads at startup. It declares the **capabilities** it provides, requires
and defines (there is no plugin "kind"). A capability is a named, versioned service contract such as `inventory.filament` v1
(see `provider-interfaces.md` for that contract's ABC and feature flags); exactly one plugin serves each capability at a time.
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

### What an installed plugin may and may not do

* UI: **`default` and `schema` tabs only.** `default` is the generated settings page; `schema` is a form/table Themis renders
  from JSON your plugin serves at `GET /api/v1/plugins/<id>/ui/<tab>` (`ui_schema`). `component` tabs (React compiled into
  Themis) are for bundled plugins. A manifest with one fails to load.
* `alias_routers` (routes at absolute paths) are bundled-only: they could shadow core routes.
* Migrations: `down()` is required. Plugin tables must start with the table prefix. Migrations run in a savepoint; a failure marks
  the plugin `error` and never stops Themis.
* A provider's methods are `async`; wrap blocking SDKs with `run_in_executor`. Core calls you through `host.call` (timeout,
  exceptions contained, state recorded) — never raise into core on purpose, but nothing breaks if you do.

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

`/api/v1/capabilities/<capability>/…` is the provider-independent path: it is forwarded to whichever plugin is selected for the
capability, so a client keeps working when the provider is swapped. Every `inventory.filament` provider serves the shared routes
(`low-stock` GET/PUT, neutral `inventory:*` scopes: `app/plugins/capabilities/inventory_routes.py:shared_router`, listed in its
`Provide.routers`). Anything a provider adds beyond that (Local inventory's `weight-log`) is optional and answers 404 from a provider
without it; nothing selected answers 409. A plugin route `PUT /provider` is never exposed there (the selection route owns it). Common
functionality lives in the neutral API (`/api/v1/inventory/*`); the old `/api/v1/spoolman/*` paths are deprecated aliases.
