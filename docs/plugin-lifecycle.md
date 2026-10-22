# Plugin data lifecycle (BIZ-302)

What disabling, uninstalling, reinstalling, upgrading, failing an upgrade and rolling back do to data, and what they never touch.
Plugins are trusted code (D17): this policy is about predictable data ownership and recovery, not sandboxing. Installation
mechanics are in [plugins.md](plugins.md); each rule below names the test that holds it (`backend/tests/plugins/test_plugin_lifecycle.py`
unless another file is named).

## Who owns what

| Data | Owner | Kept when the plugin is disabled | Kept on uninstall | Removed by "also delete this plugin's data" |
|---|---|---|---|---|
| Tables named `<table_prefix>…` (default `<id>_`), created by the plugin's migrations | **plugin** | yes | yes | yes (migrations' `down()`, newest first) |
| `plugin_schema_versions` rows for the plugin (which migrations ran) | plugin (host bookkeeping) | yes | yes | yes (deleted with each `down()`) |
| `plugin_configs` row: settings, secrets, enabled flag, state | plugin (host stores it) | yes (enabled becomes false) | yes (disabled) | yes |
| `capability_selections` rows naming the plugin | core (a choice about the plugin) | yes | yes (goes dormant) | yes (auto-select may reconsider) |
| `installed_plugins` row, code under `<data>/plugins/<id>/<version>/` | host | n/a | code removed at the restart; the row with it | n/a |
| Core tables: `printers`, `jobs`, `job_spool_snapshots`, `inventory_pending_writes`, `printer_model_slicer_profiles`, … | **core** | yes, untouched | yes, untouched | yes, untouched |
| Opaque provider references stored in core rows (`printers.plugin_id`, `spool_ref`, `material_ref`, `provider_plugin_id`) | **core**, referring to the plugin | yes | yes | yes |

Core rows reference a plugin by plain string id or ref, never by foreign key, so a plugin can go away without invalidating them.
The referent just becomes **dormant**: a printer whose vendor plugin is disabled or gone keeps its row and is shown as dormant; a
spool snapshot of a removed provider keeps its `provider`/`spool_ref` text and stays readable in the job history. The host never
silently substitutes another implementation (`docs/plugins.md` "Capability resolution modes").
(`test_disabling_a_plugin_keeps_…`, `test_core_records_that_hold_opaque_provider_refs_survive_…`,
`test_uninstall_with_data_removal_removes_only_the_plugins_own_data`.)

A migration may create tables only under the plugin's prefix **and, on `down()`, may drop only tables under it**: a `down()` that
would drop anything else is refused and rolled back, so an uninstall can never delete core-owned (or another plugin's) tables
(`test_a_down_that_drops_a_table_outside_the_prefix_…`, `test_uninstalling_with_data_removal_is_refused_when_down_would_drop_core_data_…`).
Plugins may still `ALTER` core tables or write core rows (they are trusted); that is outside what the host can police and is
reviewed at publication, not enforced.

`GET /api/v1/plugins/<id>` returns this ownership as `data`: `host_api`, `supported_host_api` (what this Themis accepts),
`table_prefix`, `tables` (the plugin's tables that exist now) and `migrations.shipped` / `migrations.applied`
(`test_the_detail_says_what_the_plugin_owns_…`). The manifest declares ownership through `table_prefix` and `migrations`, and
compatibility through `host_api` (the host refuses any other value) and optionally `min_themis`.

## Operations

| Operation | What happens | Data | Reversible? |
|---|---|---|---|
| **Disable** (`PUT /plugins/<id> {enabled:false}`) | Stops serving and receiving events immediately; its UI contributions disappear; no restart | Everything kept | **Yes**: enable it again, and it resumes as it was |
| **Uninstall, keep data** (`DELETE /plugins/<id>`, the default) | Disables now; code is deleted at the next restart | Tables, migration records, settings, secrets and selections kept; core rows untouched | **Yes**, by reinstalling (below) |
| **Uninstall + delete data** (`?remove_data=true`, an explicit choice in the dialog) | In one transaction with the uninstall mark and the audit row: every migration's `down()` newest first, then the settings row and selections | Plugin-owned data **destroyed**; core rows untouched | **No**. Refused when the plugin is not loaded (its `down()` is unavailable) or when a `down()` fails or oversteps: then nothing changes, and the plugin is left enabled as it was |
| **Reinstall** (same id) | Allowed once the restart has applied the removal | If data was kept, the plugin **reclaims it**: its migrations are recorded as applied, so they do not run again, and its tables and settings are as they were. It comes back **disabled** (the uninstall disabled it and its stored flag is kept) until an admin enables it. If data was deleted, it starts fresh | n/a |
| **Upgrade** (different version, same id) | Staged; the running version stays on disk as `previous_version`; applies at the restart: new code loads, then pending migrations run in version order | The host never deletes data on upgrade; a migration's `up()` is trusted code, and only table creation (and, on uninstall, `down()` drops) outside the prefix are guarded | Via rollback while no *new* migration has applied |
| **Failed upgrade** | See below | v1's data intact | Recoverable |
| **Rollback** | Swaps back to `previous_version` at the next restart | Kept | Roll forward again by upgrading |
| **Downgrade by uploading an older release** | Refused when the database already has migrations that release does not ship (a plugin that deliberately squashed its migrations has no in-product way past this: ship the squashed versions too, or remove the plugin's data) | Kept | n/a |

(Disable/uninstall/reinstall: `test_a_reinstall_after_the_restart_reclaims_kept_data_through_the_real_install_path`,
`tests/plugins/test_plugin_install_routes.py::test_uninstall_keeps_data_by_default_and_remove_data_drops_it`.)

## Migrations: sequencing, compatibility, failure

* Versions are **unique positive integers** (gaps are allowed); a manifest with duplicates or non-positive versions fails to
  load (`test_migration_versions_must_be_unique_positive_integers`). They run after the core migrations, per plugin in ascending
  order, each in a savepoint and recorded in `plugin_schema_versions`; re-running is a no-op (idempotent; `test_plugin_migrations.py`,
  including fresh and previously-migrated databases).
* A failing migration is **rolled back completely** (a half-created table leaves no trace), reported, and **stops that plugin's
  later versions**. The plugin is then not built (`migrations.failed`); Themis and every other plugin boot normally.
* **Compatibility is checked at three points:** install (`host_api`, `min_themis`, manifest matches the toml, dry-run import, and the
  package must ship every migration the database already has, so an older release cannot be uploaded over newer data;
  `test_a_package_that_does_not_ship_migrations_the_database_already_has_is_refused`), every start (toml vs. `MANIFEST`), and when
  the host builds the provider.

## A failed upgrade

Restarts are always an admin's call, so the recovery is too: **there is no automatic rollback**. What is guaranteed:

1. **Themis boots.** A package that fails to import, or whose migration fails, is recorded as `error` with its message on the plugin's
   row (a package that does not import is also listed with `loaded=false`; one whose migration failed stays registered but is not
   built); the rest of Themis is unaffected (`test_an_upgrade_whose_code_fails_to_load_is_reported_and_rolls_back`).
2. **Nothing is half-applied.** The failed migration left no table and no `plugin_schema_versions` row; earlier versions' data is intact.
3. **The previous version is still on disk** as `previous_version`.
4. **Recovery path:** if no *new* migration was applied by the failed version (the first new migration itself failed, or the code
   would not load), **Roll back** is accepted, the restart loads the previous version, and the plugin is `active` again with its data
   (`test_a_failed_upgrade_boots_reports_a_recoverable_state_and_rolls_back_to_the_working_version`). If some new migrations did apply
   before a later one failed, rollback is **refused**, naming the migrations, because the old code would run against a newer schema
   (`test_a_partially_applied_upgrade_cannot_be_rolled_back_and_says_to_upgrade_forward`): upgrade forward to a fixed release, or
   keep the plugin disabled; the data stays safe either way.

## Stale settings and navigation

A plugin's navigation entry, tabs and settings section exist only while it is installed, running and enabled (`docs/plugins.md`
"Declarative UI"). After disable or uninstall nothing of it is offered. Its retained settings row is not shown anywhere, and returns
with the plugin; a stored capability selection naming a gone plugin shows as *dormant* on Settings → Capabilities until the plugin
returns or the admin clears it.
