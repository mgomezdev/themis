# 3MF inspection and model preparation: ownership (BIZ-259)

Epic BIZ-265. Audit of 2026-10-12 at `develop` ea33264. Blocks BIZ-248 (Laminus contract), BIZ-256.
Behavior is pinned by `backend/tests/services/test_3mf_ownership_characterization.py` (real files in
`docs/reference 3mfs/` + synthetic archives). **No code is removed or moved by this issue**; moves/deletions are follow-ups.

## Decision

1. **Reading a file Themis already holds is local, offline, in-process.** It never needs a Laminus server. Two kinds:
   - **Generic core (stays in Themis):** OPC/ZIP package handling, plate discovery, thumbnail extraction, the
     sliced-archive plate list, filament-slot listing, project-settings *presence*. Orca/Bambu 3MF layout is the de-facto
     format of every printer family Themis supports (Bambu, Elegoo, Snapmaker), so a read-only reader of that layout is
     core, not a Laminus feature.
   - **Provider-owned (slicer vocabulary):** curated override keys + labels, override inspection against a merged
     preset config, gcode/archive estimate parsing (`; filament used [g]`, `estimated printing time`).
     They stay behind `SlicingProvider`-family methods but must be reachable with **no server configured**.
2. **Construction/preparation (arrange, pack, tool mapping, slice) is provider-owned and online-only.** Themis keeps no
   3MF *writer*. `mesh_3mf_builder.build_sliceable_3mf` / `stl_to_3mf` are pre-sidecar and have no production caller.
3. **Offline / no-Laminus behavior (product requirement, unchanged):** upload, library scan, plate list, thumbnails, model
   filaments, embedded settings, sliced-gcode estimates (queue, library, jobs) all work. Online-only: override check against
   presets (route returns `error: "Override check requires Laminus sidecar"`, `has_findings: false`), slicing, arrange/pack.
   G-code pass-through needs only the offline reader (consistent with BIZ-248).
4. **Neutral DTOs for provider inspection** (frozen dataclasses in `providers/slicing.py`; **amended by BIZ-248: they live in `providers/contract.py`, and BIZ-281 imports them from there directly, no re-export**; route JSON unchanged, see
   `contracts/response-keys.json`):

   ```
   PlateEstimate        grams: float|None, seconds: int|None, per_extruder_grams: list[float]|None
   SettingChange        key: str, from_value: str, to_value: str      # serialized as "from"/"to"
   SlotWarning          used_slots: int, printer_slots: int
   OverrideInspection   has_embedded_settings: bool, setting_changes: list[SettingChange],
                        slot_warning: SlotWarning|None, has_findings: bool   # == bool(changes or warning)
   CuratedSetting       key: str, label: str                           # keys+labels move together
   ```
   `inspect_overrides(source, merged_config, slots) -> OverrideInspection`, `parse_estimates(path, plate) -> PlateEstimate`,
   `curated_settings() -> tuple[CuratedSetting, ...]` (replaces `curated_override_keys` + the core `_SETTING_LABELS`).
   They live on an **offline `FormatProvider`** (no network, never `None`), split from the network `SlicingProvider`;
   `get_format_provider()` keeps its name and returns it. Only these three shapes cross the boundary; no Orca key names
   beyond the opaque curated keys appear in core.

## Classification

Production callers verified by grep over `backend/app` and `backend/scripts`. "Required output" is what the caller consumes.

| Symbol | Class | Production caller | Required output |
|---|---|---|---|
| `three_mf_parser.parse_three_mf` | live, generic core | `library_scanner._parse_plates` (kind `3mf`); `scripts/reextract_thumbnails.py` | `PlateInfo[]` (number, thumbnail path, time, grams) persisted to `uploaded_files.plates` |
| `parse_sliced_archive` | live, generic core (estimate fallback calls provider) | `library_scanner._parse_plates` (kind `gcode_3mf`) | same `PlateInfo[]`; one per `plate_N.gcode` |
| `parse_model_filaments` | live, generic core | `GET /files/{id}/model-filaments` | `[{index, color, type}]` |
| `parse_embedded_settings` | live, split: file read = core, key list/labels = provider | `GET /files/{id}/embedded-settings` | `[{key, label, value}]` |
| `_find_model_part`, `_plates_from_model_settings`, `_sliced_plate_meta` | live (internal), generic core | `parse_three_mf` / `parse_sliced_archive` | plate numbers / (secs, grams) |
| `_SETTING_LABELS` | live, provider-owned vocabulary in core | `parse_embedded_settings` | label text |
| `parse_three_mf` JSON `slice_info.config` branch | live code, **no real input**: Orca writes XML, so it only fires for synthetic files (reference files give 0 estimates) | same as above | estimates; follow-up F3 |
| `mesh_3mf_builder.source_has_project_settings` | live, generic core, **misfiled** | `jobs.check_overrides` | bool |
| `build_sliceable_3mf`, `stl_to_3mf`, `_parse_stl`, templates | **test-only dead** | none (`tests/services/test_mesh_3mf_builder.py` only) | n/a; replaced by provider `pack_models`/`arrange`/`slice(prepared=…)` |
| `SlicingProvider.parse_estimates` / `laminus/gcode.py` | live, provider-owned, offline | `queue_engine` (x2), `slice_saver`, `jobs` (x2), `library_scanner`, `parse_sliced_archive` | grams/seconds/per-extruder |
| `SlicingProvider.inspect_overrides` / `laminus/overrides.py` | live, provider-owned, offline-capable | `jobs.check_overrides` (after catalog + merged_config, so online in practice) | `OverrideInspection` |
| `SlicingProvider.curated_override_keys` | live, provider-owned | `jobs.py` (allowed override keys), `parse_embedded_settings` | key tuple |
| `slicer_service._inject_thumbnail`, `projects._parse_plate_nums`, `thumbnail_regen` | live, **duplicate** plate/thumbnail zip logic | slice path; pack path; thumbnail regen | PNG bytes / plate numbers; follow-up F4 |
| `project_config_builder`, `ProfileIndex`, `PresetResolver` | dead pre-sidecar (tests only) | none | BIZ-261 |

Not read by Themis at all today: per-object/instance/modifier/paint data in `model_settings.config` (only the max
`extruder` index is read, for the slot warning). Preserving it across arrange/pack is a *Laminus* guarantee (BIZ-236/240),
not a Themis reader feature.

## Follow-ups (concrete only)

- **F1 (BIZ-280)** Move `source_has_project_settings` into the core reader; `jobs.py` imports it from there. Makes `mesh_3mf_builder` fully dead.
- **Note:** `printers.py:540` calls `get_format_provider().compatible_presets`; that needs a catalog (network), so F2 must keep it on `SlicingProvider`, not the offline `FormatProvider`.
- **F2 (BIZ-281, blocked by BIZ-248)** Introduce `FormatProvider` + the DTOs above; `curated_settings()` replaces `curated_override_keys` and `_SETTING_LABELS`; routes serialize DTOs to the unchanged JSON. Update `contracts/response-keys.json` only if keys change (they must not).
- **F3 (BIZ-282)** `parse_three_mf`: replace the JSON `slice_info.config` branch with the XML reader (`_sliced_plate_meta`); update characterization test `test_json_slice_info_supplies_estimates_for_a_model_3mf`.
- **F4 (BIZ-283)** Route `slicer_service._inject_thumbnail`, `projects._parse_plate_nums`, `thumbnail_regen` through shared core-reader helpers.
- **Delete (coordinate on BIZ-261, not a new issue):** `mesh_3mf_builder.py` + `tests/services/test_mesh_3mf_builder.py` after F1; the 3 builder characterization tests here go with it.
