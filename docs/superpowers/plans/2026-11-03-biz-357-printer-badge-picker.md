# BIZ-357 Printer eligibility badge picker Implementation Plan

> Executed natively in the main session (CLAUDE.md § Development workflow). Steps use `- [ ]`.

**Goal:** One compact badge picker (yellow = group of printers: Any printer / Any <model>; blue = a specific printer) used by New Job, Edit Job and the project generate step, plus a new "Any printer" target that dispatches to every printer, including ones added later.

**Architecture:** "Any printer" is a `JobModelTarget` with `model_uuid = NULL` (migration rebuilds `job_model_targets` with a nullable `model_uuid`; SQLite can't ALTER a column). `model_targets` matching treats NULL as "every printer" (printers with no registry model included). API: `ModelTargetInput.model_uuid: str | None`, projects `GenerateRequest.eligible_any_printer: bool`. Frontend: selection keys gain `any` beside `<printer id>` and `model:<uuid>`; `PrinterTargetPicker` replaces `PrinterPicker` (NewJob/EditJob), `ModelPicker` and `PrinterEligibilityPicker`.

**Spec:** design record § Groups ("Eligible printers are opt-in badges… 'Any printer' is new (New Job gets it too)"); Linear BIZ-357; mockup Groups screen.

## Global Constraints
- Opt-in: starts empty; empty selection is flagged ("Nothing selected — nothing will dispatch"), not blocked.
- A specific printer covered by a chosen group (Any printer, or Any <its model>) is greyed out in the menu; adding "Any printer" greys every other entry. Already-added entries are greyed.
- Each badge carries its own config row (existing PerPrinterConfig, keyed by the same selection key).
- Pre-sliced (gcode) jobs keep their eligibility rules: `accepts_file` + `gcode_eligibility` apply per materialized printer as today.

## Review Focus
1. A printer added after the job, under "Any printer" → gets a config on its first claim cycle (sync_targets_for_printer). → backend test.
2. A printer with no registry model under "Any printer" → matched. → backend test.
3. A job with both "Any printer" and an explicit printer (API allows it) → explicit config wins for that printer, no duplicate (unique (job, printer)). → backend test.
4. Edit Job round-trip of an "Any printer" target (GET model_targets with model_uuid null → key `any`). → frontend test.
5. Project generate with Any printer packs for the smallest bed in the fleet. → backend test.

### Task 1: Backend "Any printer" target
Migration vNNN (next free number at implementation time) rebuilding job_model_targets with nullable model_uuid (keep FK + CASCADE); `models.py`; `model_targets.py` matching (`target.model_uuid is None or == printer.model_uuid`; sync for printers with model_uuid None too); `jobs.py` ModelTargetInput validator (None allowed, blank rejected), the per-target checks at ~L160/185/217/226 (label "Any printer"); `_target_dict` label; projects `eligible_any_printer`. Tests per Review Focus 1–3, 5. contracts + openapi.

### Task 2: PrinterTargetPicker component
`frontend/src/components/PrinterTargetPicker.tsx` + test: badges, × remove, "+ Printer" searchable menu with "Group of printers" / "Specific printer" sections, greying rules, empty warning. `ModelTargets.tsx`: `ANY_KEY`, `isGroupKey`, `buildEligibility` emits `{model_uuid: null}` for `any`, `modelConfigSource` for `any` (first printer as representative).

### Task 3: Wire into screens
NewJob + EditJob (replace local PrinterPicker + ModelPicker), ProjectBuilder + ProjectDetail (replace PrinterEligibilityPicker; send `eligible_model_uuids` / `eligible_any_printer`). Delete the replaced components. Tests + e2e update.

### Task 4: Docs, checks, review, PR.
