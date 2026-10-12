# 3MF thumbnails and metadata: Laminus vs embedded (BIZ-258)

Epic BIZ-265. Builds on `3mf-ownership.md` (BIZ-259) and `laminus-contract.md` (BIZ-248). Audit of 2026-10-12 at `develop` dd792b7.
Behavior is pinned by `backend/tests/services/test_3mf_thumbnail_behavior.py` (plus the existing `test_3mf_ownership_characterization.py`,
`test_thumbnail_regen.py`). **Investigation only: no production code changed.** Implementation is one follow-up (see Follow-up).

## Verdict

Themis does not need Laminus (or Orca) to show thumbnails. Every slicer-written file already embeds them, and the offline core reader extracts
them. `thumbnail_regen` is a **local OrcaSlicer CLI** call (`orca --slice N --arrange 0 --export-3mf`), not a Laminus call: it ignores
`LAMINUS_SIDECAR_URL`, and the Docker `app` image has no Orca binary (Orca lives in the `orca` sidecar container), so in the supported deployment it
already no-ops. It only ever helps a local-dev checkout with Orca installed, for files that embed no thumbnail at all.

| `thumbnail_regen` path | Verdict |
|---|---|
| `_render_plate_thumbnail` / `_regen_sync` (Orca CLI subprocess, `get_orca_executable`) | **Delete.** Direct-vendor-binary use bypasses the sidecar boundary; dead in Docker. |
| `regen_file_thumbnails` (DB writer, `set_session_factory`, conftest hook) | **Delete** with the above. |
| `files.py:326` upload `BackgroundTask` (3MF only; sliced `.gcode.3mf` already skipped, BIZ-190) | **Delete.** Upload/library scan keep working from embedded thumbnails; plates without one get no thumbnail (`thumbnail_url: null`). |
| `projects.py:1461` pack-output `BackgroundTask` (Job Pack 3MFs) | **Delete**, gated on one check: confirm a real Laminus pack output embeds `Metadata/plate_N.png` (no such fixture exists in-repo). If it does not, the answer is a Laminus change (`construct` should emit plate thumbnails), not Themis rendering. |
| `printers.py` "add printer file to library" | Inherits the upload path; no change of its own. |
| Embedded extraction: `parse_three_mf`, `parse_sliced_archive`, `extract_gcode_thumbnail`, `LibraryScanner._parse_plates` | **Remain (core, offline).** Confirms BIZ-259. |
| "Move behind the Laminus provider" | **None now.** No Laminus render/thumbnail endpoint exists in the contract (`contract.py` has none) and nothing needs one. Revisit only if a pack fixture shows missing thumbnails and product wants them. |

## Consumers (inventory)

| Consumer | Where | Needs |
|---|---|---|
| Library scan / rescan | `LibraryScanner._parse_plates` (library_scanner.py) | `parse_three_mf` / `parse_sliced_archive` / `extract_gcode_thumbnail`; persists `uploaded_files.plates[].thumbnail_path` into the filecache. Never launches Orca (tested). |
| Upload flow | `files.upload_file` -> scanner, then `regen_file_thumbnails` BackgroundTask for `3mf` | embedded thumbnails; regen only for plates with `thumbnail_path` unset |
| Pack output | `projects.py` ~1399: row created with `thumbnail_path: None`, plate numbers via `_parse_plate_nums` (duplicate zip logic, BIZ-283), then regen | thumbnails embedded by the packer, else none |
| Project / file views | `files._to_dict` -> `thumbnail_url`, `plate_thumbnails[]`; `GET /files/{id}/thumbnails/{name}`; frontend `FilesScreen`, `NewJobScreen`, `ProjectBuilderScreen` | optional URL; null already tolerated |
| Job plate info | `jobs.py:677` reads `plates[].thumbnail_path` | same |
| One-shot script | `scripts/reextract_thumbnails.py` | `parse_three_mf` (restores originals over regen output) |

## Tested inputs

| Input | Source | Plates | Thumbnails | Estimates |
|---|---|---|---|---|
| Ordinary model 3MF, single plate | `docs/reference 3mfs/{elegoo,snapmaker} blank single plate.3mf` (Orca-written) | 1, from `plate_N.png` | `plate_1.png` embedded (+ `_small`, `no_light`, `top`, `pick` variants, unused) | none (unsliced; `slice_info.config` is XML with no `<plate>`) |
| Multi-plate model 3MF | `{elegoo,snapmaker} blank 2 plates.3mf` | 2 | `plate_1.png`, `plate_2.png` embedded | none |
| Pre-sliced `.gcode.3mf` | **synthetic only** (no real file in repo): `plate_N.gcode` + optional `plate_N.png` + `slice_info.config` XML | one per `plate_N.gcode` | embedded per plate, else none | `slice_info.config`, else that plate's gcode summary lines (offline provider) |
| No thumbnails (gridfinity-style, bare geometry) | synthetic | from `model_settings.config` (`plater_id` or `<id>`), else StartPart -> 1 | none -> `thumbnail_path: None` | none |
| PrusaSlicer / eufyMake style | synthetic | StartPart -> 1 | one global `Metadata/thumbnail.png` / `preview.png` reused for all plates | none |
| Plain `.gcode` | `extract_gcode_thumbnail` | 1 | largest `; thumbnail begin WxH` block in the first 2 MB, else none | gcode summary |
| Missing / partial / corrupt | synthetic + reference file with `plate_2.png` removed | see below | | |

Known gap: no real multi-plate pre-sliced `.gcode.3mf` fixture exists. The follow-up must add one (exported from Orca, 2 plates) before deleting regen.

## Embedded vs from Laminus

Embedded and sufficient: plate numbers, per-plate PNG, model filament slots, project-settings presence, sliced estimates (archive/gcode).
From Laminus (online only, unchanged): arrange/pack/slice, override check against presets, profile catalog. **Nothing about thumbnails or plate lists
requires Laminus**; the sole open question is whether Laminus-*produced* artifacts (pack, slice output) embed thumbnails (slice output: yes,
`slicer_service._inject_thumbnail` / Orca's own; pack: unverified).

## Behavior without Laminus / Orca (all tested)

- No Laminus configured or unreachable: scan, upload, plates, thumbnails from embedded data all work (reader never calls the provider except `parse_estimates`, itself offline).
- Orca missing (Docker app container, most hosts): regen silently no-ops; plates stay as scanned (`[None, None]` thumbnails stay None); no exception, no files written.
- Same outcome whether `LAMINUS_SIDECAR_URL` is unset or points at a dead host: regen never consults it.
- Corrupt archive: `[]` plates, nothing written. Corrupt PNG inside a valid archive: bytes copied verbatim (no PNG validation), so the browser shows a broken image.
- Missing `.gcode.3mf` plate PNG: plate kept, thumbnail None, estimate kept.

## Version / format limitations

1. **Partial PNG loss drops plates.** `parse_three_mf` discovers plates from `Metadata/plate_N.png` first and consults `model_settings.config` only when
   *no* plate PNG exists. A 3MF with PNGs for some plates only (stripped by a tool) silently loses the others, and regen can never recover them since it
   iterates stored plates. Fix: union the sources (follow-up).
2. Layout is Orca/Bambu (`Metadata/plate_N.png`, `slice_info.config` XML, `model_settings.config`). Verified on Orca-derived Elegoo/Snapmaker files; Bambu Studio is the
   same layout by lineage but not fixture-tested. Other slicers fall back to a single global thumbnail or none.
3. PNG bytes are not validated (see above).
4. The JSON `slice_info.config` branch of `parse_three_mf` has no real producer (BIZ-282).
5. G-code thumbnail scan reads only the first 2 MB; a header larger than that yields none.

## Proposed neutral inspection result (only what Themis needs)

No new provider DTO. The core reader already returns it; name it and stop duplicating zip logic (BIZ-283):

```
PlateInfo(plate_number, thumbnail_path|None, estimated_time, filament_g)   # exists: three_mf_parser.PlateInfo
artifact kind: model_3mf | gcode_3mf | gcode                               # exists: contract.ArtifactKind, file_kind()
```
Thumbnail reference = a file in Themis' filecache, never a Laminus URL. Slicer-specific fields (preset names, Orca keys, profile ids) stay behind
`FormatProvider`/Laminus per BIZ-259. If Laminus later supplies thumbnails it returns them inside the produced artifact (`gcode_3mf`/`project_3mf`),
and Themis reads them with this same reader.

## Follow-up (one issue, parent BIZ-265)

"Remove Orca-CLI thumbnail regen; fix partial-PNG plate discovery". Scope: delete `thumbnail_regen.py` + its two `add_task` calls + conftest hook + docs/agent
mentions (`get_orca_executable` stays until BIZ-260/261 settle config); union plate discovery in `parse_three_mf`; add fixtures: real 2-plate pre-sliced
`.gcode.3mf` from Orca and a real Laminus `construct`/pack output (check for `plate_N.png`). Tests: update `test_3mf_thumbnail_behavior.py` limitation
tests to the fixed behavior; delete `test_thumbnail_regen.py`; assert upload/pack never spawn a subprocess.
Coordinate with BIZ-261 (dead pre-sidecar code) and BIZ-260 (config): the only shared surface is `get_orca_executable` in `config.py`.
