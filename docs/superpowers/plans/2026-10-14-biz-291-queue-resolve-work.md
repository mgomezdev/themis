# BIZ-291: queue pulls slicing via `resolve_work`

Epic BIZ-265. Follows BIZ-290 stage 1 (#152). Contract: `docs/architecture/laminus-contract.md` §6-7. Scoped with `themis-planning`.

## Verified current state (code wins over the issue text)
- Claim path: `queue_engine._try_claim_for_printer` (~L790-806) gates on `_laminus_down_reason()` (L828: `get_slicing_provider()` None -> "not configured", `health(2)` -> not ready / unreachable). Pre-sliced files skip it. With the provider down, a job with `allow_cached_slice` may still claim via `_cached_version_for_claim` (BIZ-201). `_run_slice_and_print` re-checks at ~L996 and releases a cache-only claim if still down.
- `get_slicing_provider()` is still read at 4 more places in the slice path (`L871`, `L1105` tool mapping, `L1179`, `slicer_service.py:144`) plus catalog/routes (`catalog_service`, `slice_saver`, `jobs`, `files`, `projects`, `printers`).
- **Cache key already carries the provider** (`slice_cache.CacheKeyInputs.provider`, BIZ-250; the default `laminus` is omitted from the hash so old keys stay valid). Staleness is tracked separately (`preset_content_hash`, `slicer_version`). So `contract.cache_key` is not a missing feature: adopting it invalidates every saved slice for engine id/version/build that staleness already covers.
- No per-printer preferred-provider column exists (`printers` has no such field; latest migrations in `backend/app/migrations/`).
- `resolve_work` returns `PASS_THROUGH` / `PRODUCE(provider_id)` / `BLOCKED(reason)`; needs `GcodeProducer`s + `ProviderStatus` (the Laminus adapter has neither yet).

## Steps (each independently shippable, behavior unchanged with Laminus only)
1. **`LaminusGcodeProducer`** (`providers/laminus/producer.py`): wraps `SlicingProvider`; `identity`, `status()` (maps `health`/`SlicingProviderNotReady` -> READY/NOT_READY/UNREACHABLE, unconfigured -> UNREACHABLE), `supports_target` (machine preset known in the cached catalog), `profiles`, `curated_settings`, `produce` (delegates to `slice`). Exposed by the plugin instance (`LaminusConnection.gcode_producer()`); the `gcode.produce` Protocol checks it.
2. **Resolution helper** `services/slice_resolution.py`: builds `{id: producer}` for enabled providers, statuses via `asyncio.to_thread(status, 2)`, `TargetPrinter` from the printer row (`bed_x/y_mm`, `model_uuid`, toolheads), `WorkItem` from the file kind, then `resolve_work`. Maps `Reason` -> the existing user-visible block messages (keep the exact current texts for no-provider / not ready / unreachable).
3. **Preference storage**: migration `vNNN_printer_preferred_slicer.py` adds nullable `printers.preferred_slicer_plugin_id`; PATCH `/printers/{id}` field; frontend select on printer settings only when >1 provider is enabled. Regenerate `openapi.json`; update `contracts/response-keys.json`.
4. **Queue**: replace `_laminus_down_reason` and the claim/dispatch gating with step 2; `BLOCKED` -> `_block_job(reason)`; `PASS_THROUGH` unchanged; keep `_cached_version_for_claim` and the cache-only release path. The dispatch resolves the provider id once and passes it through (`slice_cache.key_inputs(provider=...)`, tool mapping, `SlicerService`) instead of re-reading the global.
5. **Remaining readers** (`catalog_service`, `slice_saver`, routes): keep `get_slicing_provider()` for catalog/preset reads (capability default) unless the printer has a preference; no behavior change.
6. **FormatProvider**: resolve through the same plugin id; drop `_FORMAT_PLUGIN`.

## Invariants in play
blocked-not-failed (a provider outage blocks, never fails); head-of-line (a blocked printer's outcome must not skip ahead); pre-sliced files need no provider; cancel/stop link untouched; auth scope on the PATCH field's existing route; migrations are versioned files registered in `runner.py`.

## Open decision (default chosen)
**Cache key.** Default: keep `CacheKeyInputs` (provider already keyed, old slices stay valid) and do NOT adopt `contract.cache_key` now; add engine id/version to the key only when a second provider exists. Adopting it now would force a re-slice of every cached version. Say the word to adopt it anyway (small change in `slice_cache.key_inputs`, plus a one-time re-slice).

## Tests
Per `Reason` outcome through `_try_claim_for_printer` with fake producers (`tests/fake_providers.py`); preference persisted + PATCH + frontend; pass-through with all providers down; cache-only claim unchanged; mutation-check each branch.
