# BIZ-294 — Model -> slicer machine profile mapping with a per-printer nozzle variant

Spec: the Linear issue. Contract with the Laminus profiles API (BIZ-293): `docs/architecture/laminus-contract.md` §9.

## Design
- `printer_model_slicer_profiles(model_uuid, provider_plugin_id, nozzle_diameter_mm, flow, machine_profile, is_default)`; one default per (model, provider) (partial unique index).
- `printers.nozzle_variant` JSON (NULL = model default). `printers.current_orca_printer_profile` is retired as the make/model key and renamed `machine_profile_override` (raw, wins when set).
- Resolver `services/slicer_profiles.py`: override -> model row for (provider, variant). Missing => provider ineligible (`preference_ineligible` / `default_ineligible`) with "no machine profile for <model> <variant>".
- `job_model_targets.machine_profile` -> `model_uuid`; `model_targets` groups by `printers.model_uuid`; project generate takes `eligible_model_uuids`.
- Suggestions: optional `SlicingProvider.suggest_machines` / `nozzle_variants` (Laminus adapter calls the sidecar profiles API); route degrades to manual entry.

## Steps
1. Migration v052 (+ down, backfill) and ORM. 2. Resolver + `slice_resolution` + queue engine / jobs / files / maintenance / catalog drift / fleet backup call sites.
3. Mapping CRUD + suggestions routes; printers API (`nozzle_variant`, resolved `machine_profile`, `model_label`); retire `active-preset`.
4. Frontend: `ModelSlicerProfiles` (Settings -> Printer models, wizard step 3), `NozzleVariantPicker` (Edit printer, Fleet modal; Fleet picker removed), model-keyed targets.
5. Tests: migration (backfill/down/idempotent), API (CRUD, variant validation, default uniqueness), resolver, queue integration, adapter vs stubbed contract, frontend, contracts/openapi; docs; raise ratchets.
