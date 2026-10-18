# Plugin hardening follow-ups (BIZ-301, BIZ-302, BIZ-303, BIZ-304)

Children of the closed epic BIZ-202. Written after the audit of what already existed; the issues are mostly "make explicit and
test", so each item lists the gap found, the change, and the test that fails without it.

## BIZ-303 declarative UI contract
| Gap | Change | Test |
|---|---|---|
| `schema` tab documents were served unvalidated; no version; unknown block/field types rendered as a table/text input | `app/plugins/ui_schema.py` v1 contract, validated at registration (`ui_report`), invalid tab dropped + `ui.tab.<id>` diagnostic, `GET /ui/<tab>` 422; frontend `api/uiSchema.ts` re-validates (fail closed) | `tests/plugins/test_ui_schema.py`, `tests/api/test_plugins_api.py`, `uiSchema.test.ts`, `SchemaTab.test.tsx` |
| Settings form guessed an input for any JSON-Schema type | `settings_kind` mirrored backend/frontend; unsupported → "Not editable here", `settings.<field>` diagnostic, never sent | `test_ui_schema.py`, `PluginSettingsPage.test.tsx` |
| Undeclared setting keys were persisted | `update_config` refuses keys the model does not declare | `test_plugins_api.py` |
| No conditional visibility / constraints | `visible_if`, `min`/`max`/`max_length` | `SchemaTab.test.tsx` |
| Nav ignored `loaded` | `navigablePlugins` | `Sidebar.test.tsx` |
Decision: a transient provider error does not hide a plugin's page (it is where the admin fixes it).

## BIZ-302 data lifecycle
Policy: `docs/plugin-lifecycle.md`. Code gaps found: a `down()` could drop core tables (now refused); an older release could be uploaded over
newer migrations (refused at commit); duplicate/non-positive migration versions; a refused uninstall left the plugin disabled; **the
dry-run imported the running version when upgrading a loaded plugin (every upgrade of a running plugin failed)**; no ownership metadata
(`data` on the plugin detail). Tests: `tests/plugins/test_plugin_lifecycle.py`. No automatic rollback on a failed upgrade (restarts are
the admin's call); recovery is rollback, or upgrade forward when new migrations had already applied.

## BIZ-301 conformance
`tests/plugins/conformance.py` `Kit` + `test_provider_conformance.py` over the four core capabilities. Host changes: reject features a core
capability does not define; inventory declares `required_methods`; `required_operations(def)`; `PluginHost.rejections` surfaced in
`GET /plugins` for every mode (routed/fan-out rejections used to read "disabled"). Printer vendors serve `printer.client` through their
client class, so they get their own protocol check. Generic boundary guards (`test_plugin_id_boundary.py`, `pluginIdGuard.test.ts`), no
exceptions. Docs: provider-interfaces.md "Conformance".

## BIZ-304 matrix
`docs/plugin-test-matrix.md` (26 scenarios → tests), `test_plugin_matrix.py` (cross-boundary scenarios), `test_plugin_matrix_doc.py`
(rows must name existing, unskipped tests; every area covered).

## Verification
Backend `pytest -v -ra --cov` (coverage floor), frontend `npm run test:cov` + `npm run build`, `python scripts/export_openapi.py` (no diff).
