# BIZ-366 Plugin-reported usage v2 Implementation Plan

> Executed natively in the main session (CLAUDE.md § Development workflow). Steps use `- [ ]`.

**Goal:** Themis deducts what the printer plugin reports per loaded slot for each physical print attempt: completed, failed, cancelled or requeued. Reprints add to the job's consumption. Rollover is the plugin's concern.

**Decisions (product owner, 2026-11-05):** a plugin reports grams per loaded slot. With no report, a completed print falls back to the slicer estimate. Bambu rollover belongs to the Bambu plugin (BIZ-367). Hardware verification comes later.

**Architecture:**
- **Contract v2.** `filament_usage.UsageReport` = `{version: 2, attempt_id, revision, final, source: reported|measured, slots: [{slot, grams}]}`.
  - The report is cumulative within one attempt.
  - `slot` is the plugin's published slot id (`loaded_filaments[].slot`), never a list position.
  - Unique slot ids, at most 64 entries, grams finite and ≥ 0.
  - v1 (`basis` / `complete` / `rollover` / `breakdown`) is removed; no plugin uses it.
- **Reporter.** `JobReporter.report_usage` keeps its attempt, ownership and monotonic-revision checks, and stores the report as the plugin's `filament_usage_report` detail. A report after the attempt ended raises.
- **Attempt end.**
  - `job.complete` (completion) settles the current attempt.
  - New durable `job.attempt_ended` `{result: failed|cancelled|requeued, loaded_filaments}`, dedup `job.attempt_ended:<attempt_id>`, settles every other end of a started print. It is enqueued in the same transaction as:
    - `fail_started_job`;
    - `requeue_dead_print`;
    - cancel of a printing/paused job (jobs route);
    - printer stop reconciliation (printers route).
- **Settlement (inventory subscriber).**
  - Each report slot maps to the spool bound to that slot id in the attempt's start context, else in the end-time loaded slots; a slot found in neither is noted.
  - With no report: a completed attempt uses the slicer estimate routed through the slot plan (the existing path); any other end deducts nothing, with a note.
  - The result is frozen per attempt in `filament_accounting.attempts[attempt_id]` before any outbox write; redelivery reuses it.
  - `filament_accounting.total_grams` is the sum over the settled attempts (`jobs.actual_filament_grams` stays the sliced estimate: it feeds the estimate fallback).
- **Attempt-scoped inventory.**
  - `job_spool_snapshots.attempt_id` (NOT NULL, `''` for legacy rows); the unique key becomes (job, attempt, provider, spool). Table rebuild in v066.
  - `inventory_pending_writes.attempt_id` (nullable).
  - The "already planned" check is per attempt.
  - Starting a new attempt no longer deletes `filament_accounting`.

## Global Constraints
- Core names no vendor and keeps all provider I/O off the queue loop.
- A redelivered event never deducts twice. A later revision never changes an attempt that is already settled.
- Legacy jobs (no start context) keep the current completion path.

## Review Focus
1. A failed attempt with a report deducts it; attempt 2 then deducts on top (chained from attempt 1's pending write). → test.
2. Rollover: a report naming two slots deducts both bound spools. → test.
3. Redelivery of `job.complete` or `job.attempt_ended` doesn't double-deduct. → test.
4. A cancel or requeue with no report deducts nothing and says so. → test.
5. A report for a slot that wasn't loaded at start uses its end-time binding. → test.
