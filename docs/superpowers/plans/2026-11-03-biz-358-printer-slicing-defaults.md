# BIZ-358 Printer slicing defaults Implementation Plan

> **For agentic workers:** executed natively in the main session (CLAUDE.md § Development workflow). Steps use `- [ ]`.

**Goal:** A printer carries a default process profile used by any job config set to "Printer default", and a "Single colour only" flag that keeps multicolour work off it.

**Architecture:** "Printer default" is the existing empty `job_printer_configs.print_profile` (`""`, already the unset value in every create path). The queue resolves `config.print_profile or printer.default_process_profile` wherever it builds slice inputs; a non-presliced claim with neither blocks with an actionable reason. Single-colour-only is a per-printer eligibility verdict folded into the filament verdict (`filament_matching.evaluate_for_printer`), so the claim loop skips (not blocks), the unmatched sweep and job-detail reasons see it for free.

**Tech Stack:** FastAPI, SQLAlchemy async, SQLite migrations; React/TS.

**Spec:** `docs/superpowers/specs/2026-11-01-print-groups-pooled-packing-design.md` (branch `claude/multi-plate-3mf-repacking-s6nakq`, § Groups "Process profile per badge, or Printer default", § Colour "Single colour only"); Linear BIZ-358.

## Global Constraints

- Printer default = `print_profile == ""`; never a sentinel string.
- Multicolour config = `len(config.filament_map or []) >= 2`.
- Single-colour-only printers skip multicolour work (job stays `queued`); never block or fail for it. Fleet-wide nobody-can → existing unmatched sweep blocks.
- Missing default on a "Printer default" non-presliced config → `blocked` (never `failed`), reason names the printer and the fix.
- Migration `v057_printer_slicing_defaults` (the migration suite requires contiguous versions; draft PR #173 renumbers its own v057 when it lands).

## Review Focus

1. Pre-sliced (gcode) job with `print_profile=""` on a printer with no default must still claim (no block). → Task 2 test.
2. Explicit job process profile wins over the printer default. → Task 2 test.
3. Estimate slice (`_do_run_estimate`) uses the printer default too, else estimates fail for "Printer default" jobs. → Task 2 test.
4. PATCH with a process not compatible with the printer's machine profile → 422; explicit null clears. → Task 1 test.
5. Single-colour printer + single-colour job (filament_map of 1 or none) still claims. → Task 3 test.

---

### Task 1: Printer columns + API

**Files:**
- Create: `backend/app/migrations/v058_printer_slicing_defaults.py`; register in `runner.py`
- Modify: `backend/app/models.py` (`Printer`), `backend/app/api/routes/printers.py` (`PrinterCreate`, `PrinterUpdate`, `_to_dict`, create/update)
- Modify: `contracts/response-keys.json` (printer keys), `frontend/src/api/printers.ts` types, `openapi.json`
- Test: `backend/tests/api/test_printers_api.py`

**Interfaces — Produces:** `Printer.default_process_profile: str | None`, `Printer.single_colour_only: bool` (default False); response keys `default_process_profile`, `single_colour_only`.

- [ ] Tests: `test_patch_default_process_profile_roundtrip` (set compatible → GET returns it; null clears), `test_patch_default_process_profile_incompatible_422`, `test_patch_single_colour_only`. Catalog stubbed the way existing `get_profiles` tests do.
- [ ] Validation helper `_check_default_process(session, printer, name) -> str | None`: None/"" → None; else 422 unless `name` is in the compatible process presets for `machine_profile_for(printer)` (same compat call as `get_profiles`); catalog unavailable or machine unmapped → 422 "can't verify … map the printer's machine profile first".
- [ ] Migration: idempotent `PRAGMA table_info` guard; `default_process_profile VARCHAR(512) NULL`, `single_colour_only BOOLEAN NOT NULL DEFAULT 0`; `down` drops both.
- [ ] Run `pytest tests/api/test_printers_api.py tests/api/test_response_contracts.py -v` → PASS; commit.

### Task 2: Queue resolves "Printer default"

**Files:** `backend/app/services/queue_engine.py` (`_slice_params`, `_do_run_estimate`, `_try_claim_for_printer`), `backend/app/api/routes/jobs.py` (verify-slice/test-slice paths at the `print_profile` reads ~L536, ~L1092)
- Test: `backend/tests/services/test_queue_printer_default_process.py`

**Interfaces — Produces:** `queue_engine.effective_process_profile(config, printer) -> str` = `(config.print_profile if config else "") or (printer.default_process_profile if printer else "") or ""`.

- [ ] Tests: job with `print_profile=""` on printer with default "P-A" → `_slice_params(...)["print_profile"] == "P-A"`; explicit "P-B" wins; claim with neither → job `blocked`, `block_reason` contains printer name and "default process profile"; presliced job with neither claims normally; estimate request carries "P-A".
- [ ] Implement; block message: `f"{printer.name} has no default process profile: set one on the printer (Printers → Edit) or choose a process profile for this job"`. Check runs only for non-presliced sources, before the provider pre-flight.
- [ ] Run new + `tests/services/test_queue_engine*.py` → PASS; commit.

### Task 3: Single colour only

**Files:** `backend/app/services/filament_matching.py`, `backend/app/services/queue_engine.py` (claim loop L~844, `_no_match_reason` L~694), `backend/app/api/routes/jobs.py` (~L723)
- Test: `backend/tests/services/test_single_colour_only.py`

**Interfaces — Produces:** `filament_matching.is_multicolour(config) -> bool`; `filament_matching.evaluate_for_printer(config, printer, conflicts=frozenset()) -> Verdict` — `Verdict(False, [], [f"{printer.name} takes single-colour work only"])` when `printer.single_colour_only and is_multicolour(config)`, else `evaluate(config, printer.loaded_filaments or [], conflicts)`.

- [ ] Tests: single-colour printer skips a 2-colour job and claims the single-colour job queued behind it; same printer claims a 1-entry filament_map job; sweep blocks a 2-colour job when the only printer is single-colour (reason contains "single-colour work only"); job detail `filament_match.reasons` shows it.
- [ ] Replace the three `evaluate(...)` call sites with `evaluate_for_printer`.
- [ ] Run new + queue suites → PASS; commit.

### Task 4: Frontend

**Files:** `frontend/src/screens/PrintersScreen.tsx` (`EditForm`), `frontend/src/components/PerPrinterConfig.tsx`, `frontend/src/components/ProcessPresetPicker.tsx`
- Test: `frontend/src/screens/PrintersScreen.test.tsx` (or the existing EditForm test file), `PerPrinterConfig.test.tsx`

- [ ] EditForm: "Default process profile" select (options = `getPrinterProfiles(id).print_profiles`, first option "— none —"), under the nozzle/machine picker; "Single colour only" checkbox with hint "Takes any loaded colour, never multicolour plates". Saved via `updatePrinter` (`default_process_profile: v || null`, `single_colour_only`).
- [ ] PerPrinterConfig / ProcessPresetPicker empty option label → "Printer default".
- [ ] Tests: EditForm PATCH body carries both fields; PerPrinterConfig shows "Printer default".
- [ ] `npx vitest run` + `npm run build` → PASS; commit.

### Task 5: Docs + full checks

- [ ] `docs/agent/data-model.md` (printers columns, print_profile "" = printer default), `docs/agent/conventions.md` (single-colour skip + missing-default block), `docs/agent/backend.md` if it lists slice-param resolution; user guide printer editor section if present.
- [ ] `python scripts/export_openapi.py`; backend `pytest -v -ra --cov`; frontend `npm run test:cov`, `npm run build`, `npm run test:e2e`.
- [ ] Reviewer pass (one fresh subagent), fix, PR.

**Deferred:** "shows in project Review" → BIZ-363 (Review tab doesn't exist yet).
