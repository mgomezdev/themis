# BIZ-295 Laminus `GcodeProducer.produce()` Implementation Plan

> **For agentic workers:** superpowers:executing-plans (native, main session — CLAUDE.md). Steps use `- [ ]`.

**Goal:** Every slice (queue dispatch, estimates, verify-slice / complete-manually) runs through the neutral `GcodeProducer.produce()`
of the resolved `gcode.produce` plugin, with a `CancelToken` that job cancel and printer stop set.

**Architecture:** `SlicerService.slice(req, output_dir=None, cancel=None)` stays the core entry point (callers, cache key and block
texts unchanged) but becomes a thin translator: `SliceRequest` → `ProduceRequest` (`TargetPrinter` with `machine_profile`,
neutral `SliceSettings`) → `gcode_producer_for(provider).produce()` → path; `ProviderError` → `SliceError` (same message text) or
`SliceCancelled`. `LaminusGcodeProducer.produce()` owns name→ref resolution, curated-override validation, tool mapping and the
`SlicingProvider.slice` call. Thumbnail injection stays in core (it reads the source 3MF, not the engine).

**Spec:** Linear BIZ-295; `docs/architecture/laminus-contract.md` §2, §5, §8, §9. Laminus profiles API: `GET /api/v1/profiles/machines/{ref}/presets?kind=`
→ `{catalog_version, machine_ref, presets: [{ref, uuid, name, kind, compatible_models?}]}`; 404 `{"error": "unknown_machine_ref"}`.

## Global constraints
- Keep: BIZ-201 down-provider cached claim, cache key (`slice_cache.key_inputs` unchanged), block-message texts, pre-sliced skip.
- Core imports no vendor module; blocking I/O only in worker threads.
- `SliceSettings.overrides` ⊆ `curated_settings()` keys, else `INVALID_SETTINGS`.

## Decisions
- `SliceSettings` gains `plate_type: str | None` (bed/plate type is neutral and not a curated override; Laminus maps it to `curr_bed_type`). In `fingerprint()`.
- `SliceRequest.prepare_hook` → `tool_index` / `filament_map` (set only when the printer's client declares `slice_tool_mapping`:
  `slice_side_mapping(client, tool_index, filament_map)` replaces `tool_mapping_hook`). The producer applies them to a job-scoped copy.
- `supports_target`: `machine_profile` set AND (no cached catalog yet OR the name is in it). A cold cache must not hard-block every job at boot; the slice then reports an unknown profile as before.
- `profiles()`: Laminus `/machines/{ref}/presets?kind=` when the cached catalog gives the machine a `ref`; on any failure, or no ref, the cached-catalog `compatible_printers` filter.
- Cancel: the Laminus poll loop checks the token between polls (`SidecarCancelled`); a cancel after download deletes the artifact. Queue keeps `{job_id: (printer_id, token)}` for in-flight slices; `cancel_job` and `stop_printer` call `queue_engine.cancel_slice(...)`; `stop_printer` also cancels a job in `slicing` on that printer (cancel ↔ stop invariant). `SliceCancelled` ends the dispatch quietly (never `slice_failed`).
- Error mapping: unknown profile → `INVALID_SETTINGS`; catalog unreachable → `UNREACHABLE`; sidecar not ready → `NOT_READY`; poll/HTTP timeout → `TIMEOUT`; other → `FAILED`. Message text = the old `SliceError` text.
- **Deferred (D1 persistence, scope item 5):** jobs keep storing preset names; `SliceSettings` is built at dispatch from them. Persisting neutral settings is a schema migration across job/printer configs with no behaviour change; noted on the issue.

## Tasks
1. **Adapter cancel/timeout** — `sidecar_client.poll_status(..., should_stop=None)` raises `SidecarCancelled`; poll deadline and httpx timeouts raise `SidecarTimeout`; `LaminusSlicingProvider.slice(spec, out, *, should_stop=None)`. Tests in `test_slicing_provider_contract.py`.
2. **Producer** — `supports_target`, `profiles`, `produce` per Decisions. Tests in `test_laminus_producer.py`: each error code, cancel before / during poll, overrides (curated ok, other INVALID_SETTINGS), plate_type → `curr_bed_type`, tool_index / filament_map applied to a copy (source untouched), `want=GCODE_3MF` → export, profiles via API + fallback, supports_target matrix, resolver now returns DEFAULT_INELIGIBLE for a profile not in the catalog.
3. **SlicerService + callers** — translate as above; `SliceCancelled`; queue/jobs/estimates pass `tool_index`/`filament_map`, a token, and `bed_mm`. Update `tests/slicing_env.py` to patch `slicer_service.gcode_producer_for`; migrate prepare-hook tests.
4. **Queue cancel wiring** — `QueueEngine.cancel_slice(job_id=None, printer_id=None)`; routes call it. Tests: cancel during a slice sets the token, the job stays `cancelled`, no `GcodeFile`, printer not marked `slice_failed`; stop_printer cancels a slicing job.
5. **Docs** — laminus-contract §2/§8, provider-interfaces, agent/backend.
