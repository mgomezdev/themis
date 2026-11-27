# GH-227: failed parts survive an interruption between plate clear and repack

## Problem
Plate clear (and the History "report results" route) commits the results first: the failure rows get `resolution="requeued"`.
Then `requeue_reported` repacks the failed parts. If the process dies, or the repack raises something it does not handle,
between that commit and the repack, the parts are marked requeued but no replacement job exists and nothing is held. A retry
is refused as "already reported", so the parts are lost.

## Design
- **New column (migration v083):** `job_item_failures.requeue_pending` INTEGER NOT NULL DEFAULT 0. It holds the failed
  quantity that is still to be repacked. `record_and_restore` sets it to the failed quantity in the same transaction as the
  results. `resolution` keeps its meaning, so the UI and other queries are unchanged.
- **Exactly once, per pack:** `_queue_packs` gains an optional `on_pack_jobs(session, {item_id: qty})` hook. It runs inside
  the transaction that commits each pack's jobs. `requeue_reported` uses the hook to subtract the pack's quantity from
  `requeue_pending`, so a pack's jobs and the decrement commit together. An interruption between packs leaves only the part
  that was not packed still pending.
- **Source of truth:** `requeue_reported(session, job)` repacks whatever is still pending for that job. A repack error holds
  the remaining quantity, as it already did, and clears pending. A pool job (no repack) clears pending.
- **Recovery:**
  - Retrying the same plate-clear report, or the History report, for a job with pending rows resumes the repack instead of
    returning 409.
  - At startup, a background sweep resumes every job with pending rows.

## Tests
- The issue's scenario: interrupt at `requeue_reported` entry, retry, and assert exactly one replacement job.
- Restart sweep: interrupt, run the sweep, assert one replacement job, and assert a second sweep makes no more.
- Interruption after the first of two packs: recovery packs only the remainder, with no duplicate.
- A repack error during recovery holds the remaining quantity.
