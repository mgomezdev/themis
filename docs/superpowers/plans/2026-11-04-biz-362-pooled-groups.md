# BIZ-362 Pooled groups Implementation Plan

> Executed natively in the main session (CLAUDE.md § Development workflow). Steps use `- [ ]`.

**Goal:** A print group with strategy "pool" is one queue entry: each idle printer packs and claims as many remaining parts as fit its own bed (Laminus `/pack/next`), so mixed fleets share one project without printing anything twice.

**Architecture:**
- `project_groups.strategy = "pool"` (+ `max_parts` cap, `queue_position` set at generate). Generation creates no jobs for a pool's Split/Together items (Solo, Locked and gcode items are still generated as today).
- `jobs.pool_group_id` marks a pool plate. Remaining per item = quantity − parts reserved in the pool's non-cancelled jobs + failed parts returned (BIZ-364 rows `resolution="requeued"` on pool jobs; the requeue makes no new job for a pool item).
- `queue_engine._try_claim_for_printer` merges active pools (project queued, parts remaining) into the candidate order by queue position. On a pool: busy packing for another printer / backing off after an error / "nothing fits" remembered for this printer and remaining set → skip (fall through). Otherwise filter remaining parts to what this printer can take (filament fit via `filament_matching.evaluate_for_printer` on the item's ask; part waivers) — none → skip; else start a background pack (`_pack_pool_plate`, serialised per pool, tracked in `self._pool_busy`) and stop looking this cycle (the printer waits for its plate).
- The pack task: `ProjectConstructor.next_plate(request, out_dir, max_parts)` → (artifact, consumed per input) in a worker thread; stores the 3MF; creates a normal job (plate 1, at the pool's position, `project_item_quantities` = consumed copies, `pool_group_id`) with one config for that printer and its group-target process profile; wakes the queue. `NothingFits` → remember for (pool, printer, remaining); other errors → back off 60 s.
- Provider contract: `ProjectConstructor.next_plate` (default unsupported) + `NothingFits(ProviderError)`; Laminus: `/pack/next` with the printer's machine uuid (catalog ref of its machine profile), `X-Consumed`.
- Reporting: `GET /projects/{id}/groups` pool entries carry `pool: {done, printing, left}` and `pool_warning` when no eligible printer could take any remaining part. Queue view: pool rows with done / printing / left, claimed plates labelled with their pool.

## Global Constraints
- A claim never blocks or fails a pool job: every pool problem is a skip; the pool keeps its place.
- No part is reserved twice: remaining is computed from committed jobs; one pack per pool at a time.
- The pack call never runs on the event loop (worker thread) and never holds a DB session open across it.

## Review Focus
1. 180 / 256 / 350 mm printers on one pool → each gets a plate sized to its bed; reserved total ≤ quantity. → test.
2. A printer nothing fits → skips the pool and claims the next job. → test.
3. Busy pool (packing for printer A) → printer B skips it. → test.
4. A failed pool part goes back into the pool (remaining rises), no extra job. → test.
5. Pack error → backoff, then retry. → test.
