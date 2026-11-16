# Preset overrides for sliced jobs

**Goal:** settings overrides survive Themis routing a job to a different printer/preset, and can be set on the job,
the project's generated jobs, and the printer. Pre-sliced G-code is never touched.

**Precedence** (lowest → highest): printer bed type < `printers.slice_overrides` < the 3MF's own preset changes <
`jobs.overrides`.

## Design

- **3MF overrides** = keys in `project_settings.config` → `different_settings_to_system[0]` (Orca/Bambu: the process
  preset's changes vs its system preset) with the file's values. Filament/printer diffs are not carried (material- and
  machine-specific). Preset bookkeeping keys (`print_settings_id`, `inherits`, …) skipped. Read by
  `FormatProvider.embedded_overrides` (Laminus impl in `providers/laminus/overrides.py`); core stays vendor-neutral.
- **Merge in Themis**, not Laminus: `services/slice_overrides.extra_config(...)` builds `SliceRequest.extra_config`, which
  Laminus already merges after resolving the routed printer's presets. Bonus: everything is in the slice-cache key.
- **Laminus**: clamp Bambu `-1` "auto" values (`raft_first_layer_expansion`, `tree_support_wall_count`) after
  `extra_config` merge — a carried Bambu 3MF change would otherwise make Orca reject the project.
- **Printer overrides**: new `printers.slice_overrides` JSON (v057), curated keys only, API create/patch/out.
- **Project**: `GenerateRequest.overrides` → each generated job's `overrides`.
- **UI**: `GET /api/v1/jobs/override-settings` (curated key/label/kind/options) feeds `SliceOverridesEditor` on New Job,
  Edit Job, the printer editor, and both project generate panels. Embedded-settings rows marked `carried`.
- **check-overrides**: diffs the 3MF against the preset *with* printer/3MF/job overrides applied, so carried changes are
  no longer flagged as lost.

## Touch points

`queue_engine._slice_params/_slice_request` (dispatch + claim-time cache check), `run_estimate`,
`jobs._build_slice_request` (verify-slice, complete-manually), `jobs.check_overrides`, `projects._generate_project`,
`printers` create/update/`_to_dict`, `three_mf_parser.parse_embedded_settings`.

## Tests

`tests/services/test_slice_overrides.py` (3MF diff reader, precedence incl. supports/brim example, real dispatch path),
`tests/api/test_job_overrides.py` (override-settings, printer overrides API, check-overrides precedence),
`tests/api/test_projects_api.py` (generate overrides), `tests/test_v057_migration.py`, `tests/test_embedded_settings.py`;
frontend `SliceOverridesEditor.test.tsx`, printer edit form, project generate. Laminus: clamp unit test.

## Port onto develop (merge of `develop`, 2026-11-15)

`develop` moved to a neutral `GcodeProducer.produce()` whose `SliceSettings.overrides` must be curated keys, plus
per-target config overrides (v070) and print-group overrides (v068). Adapted design:

- Core sends the job's input (`jobs.overrides` + the per-target config's) as `extra_config` and the printer's as the new
  `SliceRequest.printer_overrides` → `SliceSettings.printer_overrides`. The **Laminus producer** reads the 3MF it slices
  (`embedded_process_overrides`) and merges bed < printer < 3MF < job. No core-side 3MF read at slice time (and so no memo).
- Slice-cache key gains `printer_overrides` (hashed only when set; the 3MF's changes follow from the source hash).
- Migration renumbered v057 → **v072**.
- Project-level overrides = **print-group overrides** (already persisted and copied onto jobs on develop): the editor
  lives in `PrintGroupsCard`; the `GenerateRequest.overrides` field is dropped.
