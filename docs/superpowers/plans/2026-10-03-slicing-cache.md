# Slicing cache (BIZ-189) — implementation plan

Linear epic BIZ-189, sub-issues BIZ-190…196 hold the full acceptance criteria; this file is the execution order and
the decisions the code relies on. Delivered as stacked PRs into `develop` (each based on the previous branch).

## Decisions (from the issue thread, 2026-10-03)

- Cached gcode is a normal library file saved next to the model; its display name *is* its filename.
- Cache key = sha256 of canonical JSON: source file content hash, plate, machine/process/filament preset names (ordered),
  `extra_config` (bed type + job overrides), `tool_index`, `filament_map`, artifact kind. Filament colour is not in the key.
- Stale = resolved preset contents hash **or** OrcaSlicer version differs from slice time. Never part of the key.
  Setting `queue_config.slice_cache_use_latest_settings` (default on): on ⇒ automatic reuse reslices a stale version;
  off ⇒ stale version still prints, flagged stale. A version picked by hand in New Job is always honoured.
  Sidecar unreachable ⇒ stale unknown ⇒ used.
- A version is saved when the production slice succeeds (not gated on print outcome); duplicates (same key) skipped.
- Any non-terminal job can be flagged "save sliced gcode"; already-sliced jobs save immediately.
- Project generation: `allow_cached` (default on) reuses an identical earlier pack (recipe hash) and lets each job use a
  cached version when a printer claims it; `save_slice` flags every generated job.
- Every cache decision logs one `slice_cache event=…` line and is stored on `jobs.slice_cache_info`.
- Model delete with versions: 409 listing them unless `?versions=delete|keep`. Moving a model moves its versions.

## PRs

1. **BIZ-190 — sliced-archive file kind.** `library_scanner.file_kind` / `is_presliced_file` / `presliced_suffix`;
   `three_mf_parser.parse_sliced_archive` (plates from `Metadata/plate_N.gcode`, thumbnails from the archive, no regen);
   `_parse_gcode_estimates(path, plate=)`; `AbstractPrinterClient.sliced_archive_supported` (Bambu True);
   `model_targets.accepts_file` replaces `accepts_raw_gcode`; `_stage_gcode` keeps `.gcode.3mf`; file dict `kind`;
   FE `lib/fileKind.ts` (New/Edit Job treat `.gcode.3mf` as pre-sliced).
2. **BIZ-191 — data model.** Migration: `sliced_versions` table; `jobs.save_slice`, `jobs.save_slice_name`,
   `jobs.allow_cached_slice`, `jobs.sliced_version_id`, `jobs.slice_cache_info`; `queue_config.slice_cache_use_latest_settings`;
   `uploaded_files.pack_recipe_hash`. `services/slice_cache.py`: `cache_key`, key inputs, stale check (sidecar
   merged-config hash + `/api/health` slicer version), `log_event`. Settings route exposes the flag.
3. **BIZ-192 — save.** `save_slice`/`save_slice_name` on create/PATCH configs; `PATCH /jobs/{id}/save-slice`;
   generate `save_slice`; queue engine copies the production artifact into the model folder and registers file + version.
4. **BIZ-193 — reuse.** `GET /files/{id}/sliced-versions`; create-from-version (`sliced_version_id` on POST /jobs);
   dispatch-time lookup when `allow_cached_slice`; project pack reuse + real `content_hash` for packs.
5. **BIZ-195/196 — library.** Delete/move semantics, scanner detach on edit, `kind` filter, `sliced_version_count` badge,
   sliceable-only guards.
6. **BIZ-194 — UI.** New Job save toggle + cached-version prompt, queue/job-details toggle + markers + debug block,
   generate dialog toggles, settings checkbox.

## Checks per PR

`pytest -v -ra --cov` (backend), `npm run build`, `npm run test:cov`, `npm run test:e2e`,
`python scripts/export_openapi.py` + no diff, `contracts/response-keys.json` for new consumed keys, `docs/agent/*` updated.

## Known follow-ups (deliberately not in these PRs)

- **Claim-time Laminus health gate vs cached jobs.** A job with `allow_cached_slice` is still blocked by the claim's
  sidecar health check during a sidecar outage, even if a usable cached version exists. Skipping the gate would let a
  cache *miss* fall through to a slice that fails (and marks the config `slice_failed`, needing an unblock), which is
  worse than waiting. Fixing it properly means doing the lookup at claim time; left for a follow-up if outages matter.
- **Index freshness.** The library index is refreshed by rescans, not a watcher. The cache re-hashes a model / pack /
  cached file whose size or mtime moved since it was indexed before trusting its hash (dispatch, generate, lookup), so
  an in-place edit is never served stale; other library views still show the indexed hash until the next rescan.
