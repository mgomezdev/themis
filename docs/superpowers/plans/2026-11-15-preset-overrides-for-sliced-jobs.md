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
