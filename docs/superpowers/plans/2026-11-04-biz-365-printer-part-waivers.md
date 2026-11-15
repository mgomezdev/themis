# BIZ-365 Printer part waivers Implementation Plan

> Executed natively in the main session (CLAUDE.md § Development workflow). Steps use `- [ ]`.

**Goal:** A printer that keeps failing a part stops getting it (in every project); a printer with many waivers is flagged, or stopped; a test print checks a fix before the waiver is cleared.

**Architecture:** `part_waivers(printer_id, part_key, file_id, failures, last_failure_at, created_at)` — `part_key` = `<file content hash>:<item kind>:<source_ref>` (`services/part_waivers.part_key(item, file)`), so the same STL / 3MF object / plate in another project is the same part. `print_results.record_and_restore` upserts a waiver when this printer's failures of the part reach `queue_config.waive_after` (default 2). The queue claim loop skips a candidate job carrying a waived part (fall-through, like a filament mismatch) and the unmatched sweep reports one every eligible printer has waived. Per printer: `waiver_flag_at` / `waiver_stop_at` (0 = off) — stop makes the printer claim nothing but its test print; Fleet rows carry `waived_parts` + `waiver_flag`. Test print: `POST /printers/{id}/waivers/{wid}/test-print` requeues one copy of the part (BIZ-364 `requeue_parts`) for this printer only with its default process profile and sets `printers.test_print_job_id`; while set, the printer claims only that job. Its plate-results carry `test_print_waiver_id`; the dialog offers "Clear the waiver".

## Global Constraints
- Completing maintenance never clears waivers; only Clear / Clear all / a test print's results do.
- 0 = disabled for both printer thresholds (the default).
- A waiver never fails a job: waived printers skip it; the sweep blocks a job no eligible printer can take.

## Review Focus
1. Two failures of a part on A (two different projects) → A waived for it in both. → test.
2. A waived printer skips the job and claims the next one. → test.
3. Stop threshold: A at its stop count claims nothing — except its test print, which jumps the queue. → test.
4. Clearing restores A. → test.
5. Flag threshold surfaces on /fleet. → test.
