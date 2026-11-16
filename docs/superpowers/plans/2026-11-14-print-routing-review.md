# Print routing for projects: review against develop (2026-11-14)

Scope: how a project's N parts reach printers (pooled print groups, BIZ-362), and how troublesome parts and printers are
caught (print results on plate clear BIZ-364, printer part waivers BIZ-365). Reviewed on `develop`.

## How it works
- A print group with strategy `pool` is one queue entry. When a printer is idle with a clear plate, the claim loop packs
  the pool's remaining parts that printer can take (filament, waivers) into the fullest plate for ITS bed (Laminus
  `/pack/next`) and queues that plate for it alone. `fixed` groups are packed once at generate.
- Clearing a plate asks what printed, per part. Failed parts go back (pool) or are repacked (fixed group). The answers drive:
  - `waive_after` (Settings): a printer that failed a part N times is waived for it, in every project;
  - `part_retry_limit` (Settings): a part that failed on N different printers is held for a person (Reprint / Drop);
  - `waiver_flag_at` / `waiver_stop_at`: a printer waived for N parts is flagged / takes no more work.

## Decisions (owner, 2026-11-14) and where they stand
| Decision | Status |
|---|---|
| Late packing, plates optimised per printer | exists (pool groups); new groups now default to `pool` — a setting (`queue_config.default_group_strategy`, Settings -> Print defaults); existing groups and slicer-import groups keep `fixed` |
| Global setting to pull a failing printer out of the queue | was per-printer only -> added fleet-wide `queue_config.waiver_flag_at` / `waiver_stop_at` (a printer's own value wins) |
| Printer rejects specific parts | exists (waivers; automatic after `waive_after` failures, cleared by a person or a good test print) |
| Global setting to flag a part by the number of printers rejecting it | exists (`part_retry_limit`: held after failing on N different printers) |
| Use the plate pass/fail confirmation as the input | exists (plate-clear results) |

## Defects found and fixed
1. A pool plate whose print FAILED and was cleared without results (QR / home-automation clear, or nobody reported) kept
   its parts reserved forever: the project came out short with the pool showing nothing left.
   Fix: clearing the plate without results releases the plate's parts back to the pool (`jobs.outcome = "unreported"`);
   until that clear they stay reserved, so another printer never repacks parts someone may still report as good. A
   released plate can no longer be reported, reviewed or requeued (409).
2. A pool plate cancelled before printing (slice failure, block, upload/start error) returned its parts and the same
   printer packed them again on the next cycle, indefinitely: one library file, one cancelled job and one notification per
   cycle. Fix: `_pool_plate_dropped` keeps those items from that printer for 15 minutes.

3. A lower-ranked printer deferred to a preferred printer that its waivers had taken out of the queue (or that a dropped
   plate keeps the parts from): the pool stalled. Fix: `defer_pool` skips both.

## Open (not changed)
- A printer's own threshold of 0 now means "use the fleet's": one printer cannot be exempted from a fleet stop except by
  giving it a higher limit of its own.
- `pools.warning` and the unmatched sweep still count a waiver-stopped printer as able to take work.
- With `pool` as the default, generate queues no jobs and does not need the project builder to be up: a builder that is down or
  lacks `next_plate` shows only as a pool that never packs (60 s backoff, log warning).
- A `fixed`-group plate whose print failed and was cleared without results is not requeued (it stays a failed job in the
  project); only reported results requeue.
- A completed plate cleared without results credits nothing to `quantity_completed` (pool "done" under-counts until someone
  reviews it); its parts stay reserved, so nothing is printed twice.
- Cancelled pool plates leave their packed 3MF in the library (`Job Pack 3MFs/pool-<id>/`).
- The dropped-plate memory is in process: a restart retries once.
