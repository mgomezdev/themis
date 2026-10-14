# BIZ-290: Laminus slicing via the plugin host (`gcode.produce`)

Epic BIZ-265. Contract: `docs/architecture/laminus-contract.md` (§1, §6). Follows BIZ-288 (plugin builds the adapter).

## Findings (before)
- `gcode.produce` is not a defined core capability; no plugin `provides` it. `PluginHost` already has choose-one machinery
  (`resolve`, `call_choose`, `eligible_providers`) but nothing uses it for slicing.
- Core resolves slicing through the module registry `slicing.ACTIVE_PROVIDER` / `get_slicing_provider()` / `slicing_provider_name()`
  (~12 call sites: queue_engine, slicer_service, catalog_service, slice_saver, routes jobs/files/projects/printers, main, slice_cache).
- No per-printer / capability-default provider storage exists beyond the host's selection row; `resolve_work` has no caller.
- Cache identity: `slice_cache.CacheKeyInputs` (provider name only). `contract.cache_key` is a new format (old rows would miss).

## Staging
**Stage 1 (this change): host-resolved slicing, registry removed. Behavior-preserving.**
1. Define core capability `gcode.produce` (mode `choose_one`) in `plugins/capabilities/`.
2. Laminus plugin `provides` it; its instance (`LaminusConnection`) is the serving part and exposes `slicing_provider()`.
3. `get_slicing_provider()` / `slicing_provider_name()` / `configured_server_url()` resolve through the host
   (`plugin_host.resolve("gcode.produce")` -> that plugin's instance); `ACTIVE_PROVIDER` and the `_REGISTRY` slicing registry go.
   Call sites keep the same accessor (no behavior change), so the ~12 sites need no edit.
4. Tests: resolution via host (disabled plugin -> None, unconfigured -> None, name follows the resolved plugin id), boundary tests green.

**Stage 2 (separate issue, needs a product decision): queue uses `resolve_work`.**
Requires persisted per-printer preferred provider, `GcodeProducer` wrapper (`supports_target`, `profiles`, `produce`), `ProviderStatus`
preflight replacing `_laminus_down_reason`, and the `contract.cache_key` migration. Not started: only one slicing provider exists, so
it changes no behavior today, and it adds a schema + UI surface.

## Verification
`pytest -v -ra --cov` in `backend/` (boundary tests included); mutate the host-resolution line and confirm a test goes red.
