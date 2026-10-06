# Plugins: authoring and installing

A plugin is an in-process Python package that Themis loads at startup. Today there is one **kind**, `filament_inventory`
(see `provider-interfaces.md` for its ABC and capabilities); the host, package format and installer are kind-agnostic.
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
kind = "filament_inventory"      # must be a kind this Themis knows
host_api = 1                     # the host refuses any other value
entry = "acme_inventory:MANIFEST"   # module:attribute
min_themis = "2026.10"           # optional, dotted-number compare with the running version
publisher = "Acme"               # optional; display only, NOT verified
# permissions = []               # reserved (BIZ-200): parsed, ignored
```

Unknown keys are rejected. The `MANIFEST` must agree with the toml on `id`, `version`, `kind` and `host_api` (checked at install
and at every start). The top-level package name must not already resolve in Themis (so a plugin cannot shadow `app`, the
standard library, a Themis dependency, or another plugin).

`PluginManifest` (see `app/plugins/manifest.py`): `settings_model` (pydantic; generates the settings form), `secret_fields`
(write-only), `factory(settings) -> provider`, `capabilities`, `ui` (`UiContribution`: a `section` on Settings → Plugins or its own
`page` with tabs), `routers` (mounted under `/api/v1/plugins/<id>/…`), `migrations`, `table_prefix` (default `<id>_`).

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
archive's SHA-256 and a full-trust warning; you confirm explicitly. Nothing is active until you **restart**.

Pipeline (`app/plugins/installer.py`), the same for both sources:

1. **Fetch.** Upload streams to disk under a size cap (25 MB). GitHub: the ref is resolved to a commit SHA through the API and that
   commit's tarball is downloaded from codeload — the moving ref is never installed. No `git` binary involved.
2. **Safe extract** into `<data>/plugins/.staging/<token>/`: absolute paths, `..`, backslashes, symlinks, hard links, device files
   and encrypted entries are rejected; at most 3000 files and 80 MB expanded (counted as bytes are written, so a lying header
   cannot bypass it).
3. **Validate** the toml (id format, reserved ids, semver, `host_api`, known kind, entry shape and file, `min_themis`, name clash).
4. **Dry-run import in a subprocess** (clean environment, throwaway data dir, `vendor/` after Themis's own path): import errors,
   missing dependencies and a MANIFEST that disagrees with the toml fail here, without touching the live process.
5. **Commit:** move to `<data>/plugins/<id>/<version>/`, write the `installed_plugins` row (`pending_restart`), audit-log it. A
   failure at any step removes the staging directory and leaves no row.

Installing a **different version of an installed id** is an upgrade: the old version stays on disk as `previous_version` (older
ones are pruned). Re-installing the *same* version is refused — bump `version`.

### Restart (always the admin's call)

Install, upgrade, rollback and uninstall only **stage** their change (disk + `installed_plugins`). Changes stack; the Plugins page
shows one banner — "Restart Themis to apply N pending changes" — and one **Restart** button, which warns when a printer is
printing. `POST /api/v1/system/restart` exits the process cleanly; Docker's `restart: unless-stopped` (set in
`docker-compose.yml`) brings it back. On a bare-metal dev run, restart it yourself. Because staging is durable, a crash or an
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
  `down()`, newest first, and drops its settings row and provider slot). Bundled plugins can be disabled, never uninstalled.

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
subprocess only protects the live process from import-time crashes, not from malicious code. Mitigations that are in place:

* Every action above needs an **interactive admin**: the keyless local admin, the bootstrap key, or an admin login session. An API
  key — even with `settings:write` — gets 403, so a leaked automation key cannot install code (`auth.require_admin_session`).
* Pinned commit SHAs (GitHub), the archive SHA-256 shown before confirming, no automatic updates.
* Every install, upgrade, rollback, uninstall and restart is written to the append-only `audit_log` table (actor, action, target,
  detail incl. source and hashes). There is no UI for it yet.
* Installation is always allowed — there is no kill switch or allowlist (decision D15).

Out of scope (tracked): private GitHub repos (BIZ-199), declared permissions (BIZ-200), hot-loading, remote frontend components.
