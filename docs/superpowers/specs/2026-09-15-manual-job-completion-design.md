# Manually completing a job without printing — design

Status: approved, ready for implementation plan.

## Motivation

Sometimes a print happens outside Themis's control — printed before Themis tracked it, printed
manually while debugging, or a job is stuck/hung in Themis's own tracking while the physical printer
genuinely finished. The operator needs a way to short-circuit a job straight to "complete" — with
realistic filament/time usage and a real Spoolman deduction — without Themis ever sending anything to
the printer.

This is a new action on the existing Job Detail page, not a new job-creation flow. A job is created
normally (attach file, pick eligible printers, etc. — unchanged); this feature only adds a way to
finish it early.

## Backend

### Endpoint

`POST /api/v1/jobs/{job_id}/complete-manually`, scope `jobs:write`, body `{"printer_id": int}`.

- 404 if the job doesn't exist.
- 404 if no `JobPrinterConfig` exists for `(job_id, printer_id)` — same check `verify-slice` already
  makes; the printer must be one of the job's configured eligible printers.
- 409 if the job's status is already terminal (`complete`, `cancelled`, `failed`).
- Otherwise proceeds regardless of current status, including `printing`/`uploading` — this
  deliberately allows short-circuiting a job Themis currently has a live connection to. Themis's own
  printer-connection state is not touched (see "Printer interaction" below); only Themis's bookkeeping
  changes.

### Flow

1. Set `status="slicing"`, `assigned_printer_id=<printer_id>`, `block_reason=None`, `updated_at=now`.
   Commit, broadcast the job (same `_broadcast_job` used elsewhere) so the UI shows "Slicing…"
   immediately, matching the normal slice UX.
2. Run a real OrcaSlicer slice for that printer/config, **in an isolated output directory** — the same
   pattern `verify-slice` already uses (`<data_dir>/gcode_verify/<job_id>` today; this feature needs its
   own isolated subdirectory, e.g. `<data_dir>/gcode_manual_complete/<job_id>`, cleaned up in a
   `finally` block same as `verify-slice`). This is deliberate: it must never touch the job's
   *production* gcode path, because if the job is currently `printing`/`uploading`, a live
   upload/print may still reference that path — the isolated slice can't collide with it.
   - Build the `SliceRequest` the same way `verify-slice` does (resolve the matched filament slot,
     `prepare_hook` for AMS remap if the config has `tool_index`/`filament_map`, `plate_config` from
     the printer's build plate + job overrides).
   - Route it through `queue_engine.run_verify_slice`-equivalent handling (the same serialized
     `_slice_queue`, not a dedicated executor) so it doesn't compete with upload/print I/O.
3. **On slice failure:** same handling as `_handle_slice_failure` — mark the job's
   `JobPrinterConfig.slice_failed=True` with the error, transition the job to `status="blocked"` with
   `block_reason=<error>`, broadcast, return `{"ok": false, "error": "..."}`. No further side effects.
4. **On slice success:** parse real usage via the existing `_parse_gcode_estimates(gcode_path)` (grams,
   seconds, per-extruder breakdown) — identical to how `_run_slice_and_print` computes actuals today.
   Delete the isolated gcode file (never printed, nothing to keep). In one transaction:
   - `job.actual_filament_grams`, `job.actual_seconds`, `job.actual_filament_breakdown` = the parsed
     values.
   - `job.status = "complete"`, `job.completed_at = now()`, `job.outcome = None` (unreviewed — if the
     job has project items, the existing `PUT /jobs/{id}/outcome` review step still applies exactly as
     it does for a real completion).
   - `job.deduction_skipped` = `False` if a deduction will actually fire (see below), else left as-is.
   - Printer bookkeeping, mirroring `handle_print_complete`: `printer.lifetime_job_count += 1`,
     `printer.lifetime_print_seconds += job.actual_seconds or 0`, `printer.awaiting_plate_clear = True`
     (DB row) plus `printer_manager.set_awaiting_plate_clear(printer_id, True)` (in-memory) — the same
     two places `conventions.md`'s invariant requires staying in sync. This makes the printer show as
     needing a manual "Ready for new work" clear in Fleet, exactly as if it had really just finished —
     even though physically it may still be running (queue eligibility also requires `is_idle`, which
     only goes true when the vendor client's own telemetry says so, so nothing will actually claim this
     printer for a new job until it's genuinely free).
   - Commit.
5. **Spoolman deduction**, fire-and-forget via the existing `_deduct_spool` helper — same resolution
   logic `handle_print_complete` uses: only if `actual_filament_grams is not None`, Spoolman is
   configured+enabled, and the matched filament slot (`_slot_for_config`) has a `spoolman_spool_id`.
6. Return the updated job (same shape `getJobDetails` returns, so the frontend can just re-render).

**Deliberately not fired:** webhooks (`job.complete`) and push notifications (Discord/ntfy/email) — a
manually-backfilled completion shouldn't trigger the same alerts a real one does.

**Printer interaction:** no command of any kind is sent to the printer/vendor client. If the job was
`printing`/`uploading` and the printer's vendor client later reports a real completion for it, the
existing `handle_print_complete` query (`Job.status == "printing" AND Job.assigned_printer_id ==
printer_id`) simply won't find this job anymore (it's already `complete`) and silently no-ops — no
double-processing, no code change needed there.

## Frontend

**New API function** in `frontend/src/api/queue.ts`, alongside `verifySlice`:

```typescript
export async function completeJobManually(
  jobId: number,
  printerId: number,
): Promise<ApiJobDetails> {
  return request(`/api/v1/jobs/${jobId}/complete-manually`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ printer_id: printerId }),
  });
}
```

**`JobDetailScreen.tsx`**: a "Mark as already completed" button, shown whenever `job.status` is not
`complete`/`cancelled`/`failed` (i.e. `queued`, `blocked`, `sliced`, `slicing`, `uploading`, `printing`,
`paused`) — reuses the existing `cancellable`-style status-set pattern already on this screen.

Clicking it opens an inline confirm panel — same shape as the Regenerate/Revoke confirm already built
for project share links (`ProjectDetailScreen.tsx`): a warning line ("This will mark the job complete
without actually printing it — this can't be undone.") plus Confirm/Cancel. If `job.printer_configs`
has more than one entry, a printer selector appears in the panel, defaulting to `job.assigned_printer`
if set, otherwise the sole entry when there's only one. Confirm calls `completeJobManually`, shows
"Slicing…" while the request is in flight (the call blocks until the slice finishes or fails, same as
`verifySlice` does today — no new polling/websocket handling needed), and on success re-renders the job
from the response (or navigates back to the queue, matching Cancel/Unblock's behavior). On failure,
shows the returned error inline, matching the existing `error` state pattern on this screen.

## Testing plan

**Backend (TDD):**
- Happy path: job in `queued` with one printer config → complete-manually → job ends up `complete`,
  `completed_at` set, `actual_filament_grams`/`actual_seconds` populated from a real (mocked) slice,
  printer's `awaiting_plate_clear` true, `lifetime_job_count`/`lifetime_print_seconds` incremented.
- 404 for unknown job / unknown printer config.
- 409 for a job already `complete`/`cancelled`/`failed`.
- Works from `printing` status too (the "any non-terminal status" case) — confirm no attempt is made
  to contact the printer/vendor client.
- Slice failure → job ends up `blocked` with the error, `JobPrinterConfig.slice_failed=True`, no
  printer bookkeeping or Spoolman deduction fired.
- Spoolman deduction fires with the correct grams/spool_id when configured; does not fire when
  Spoolman is disabled, unconfigured, or the matched slot has no `spoolman_spool_id`.
- No webhook/notification calls happen on either success or failure path (mock and assert not-called).
- The isolated slice output directory is cleaned up in both the success and failure case.

**Frontend:**
- Button appears for each non-terminal status, not for `complete`/`cancelled`/`failed`.
- Printer selector appears only when there's more than one `printer_configs` entry; pre-selects
  `assigned_printer` when set.
- Confirm calls the endpoint with the right `printer_id`; success and failure states render correctly.

**Docs:** update `docs/agent/data-model.md`'s `jobs` section and `docs/agent/backend-review.md` if this
introduces a new pattern worth flagging (e.g. another "isolated slice directory" convention alongside
`verify-slice`'s).
