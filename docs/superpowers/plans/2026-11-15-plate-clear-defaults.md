# Plate clear and failed prints: one rule, visible defaults (2026-11-15)

Branch `feature/plate-clear-defaults`, stacked on the print-routing review branch, so it can be dropped on its own.

## Today
A plate leaves a printer in one of four ways. What happens to its parts depends on how it is cleared:

| Print ended | Cleared in the Fleet dialog (results given) | Cleared with no results (QR code, home automation, API) |
|---|---|---|
| complete | good parts credited; failed parts requeued or held | nothing recorded: parts are never credited, the project never reads as done |
| failed | same dialog, but it opens on "nothing failed": one click credits a failed plate as good | fixed group: parts lost. Pool: parts go back, no failure recorded |
| cancelled by a person | not asked | nothing (fixed: not requeued; pool: back in the pool) |

Gaps: the outcome of a no-results clear is implicit and differs by case; a plate nobody reported can never be reported
later (History only offers a count-only "Mark" for completed jobs, which never reprints failed parts); a printer that
fails plate after plate with no-results clears is never stopped (waivers need per-part reports).

## Rule
**Every finished project plate gets a result. A person's report always wins; when a plate is cleared without one, a default
applies. The default is named in Settings, shown on the job, and can be changed.**

## Flow
1. **Fleet "Ready for new work"** (unchanged entry point): a finished project plate opens the results dialog. A FAILED
   print now opens on "whole plate failed" and says so; a completed one on "nothing failed". One click confirms.
2. **Clear without results** (QR / automation / API, or several finished plates stacked on one printer): each finished,
   unreported project plate of that printer gets the default for how its print ended:
   - completed -> `plate_clear_complete`: **`assume_good`** (default): every part credited, job marked *Assumed OK*;
     or `ask`: left *Awaiting results*.
   - failed -> `plate_clear_failed`: **`assume_failed`** (default): every part goes back (pool) or is repacked (fixed
     group), job marked *Assumed failed*; or `ask`: left *Awaiting results* (its parts stay reserved, nothing is reprinted).
3. **Assumed results are weaker than reported ones.** They move parts, but do not count toward a printer's part waivers or
   a part's hold: nobody said which part failed.
4. **A printer that keeps failing is paused.** After `pause_after_failed_prints` (default 3, 0 = off) failed prints in a
   row, the printer's queue is switched off (the existing Queue on/off toggle) with a visible reason and a notification.
   Switching the queue back on resumes it. This is the brake for the no-results case; reported failures keep using waivers.
5. **History is where a result is given late or corrected.** A finished project plate that is *Awaiting results* or
   *Assumed OK* can be reported from History with the same per-part dialog, and failed parts are requeued exactly as at
   plate clear. *Assumed failed* cannot be changed (its parts are already being reprinted) and says so. Reported results
   stay final, as today.
6. **Cancelled by a person**: unchanged, and now written down: a cancel is a decision, not a print result.

## Settings (Settings -> Print defaults, section "When a plate is cleared without results")
| Setting | Values | Default |
|---|---|---|
| A finished print | Assume every part is good / Leave it for me to report | Assume good |
| A failed print | Assume the whole plate failed and print it again / Leave it for me to report | Assume failed |
| Pause a printer after N failed prints in a row | 0-20, 0 = off | 3 |

## Data
- `queue_config.plate_clear_complete` (`assume_good` | `ask`), `plate_clear_failed` (`assume_failed` | `ask`),
  `pause_after_failed_prints` (int); `printers.queue_paused_reason` (text, cleared when the queue is switched on). v074.
- `jobs.outcome`: `reviewed` (reported) | `assumed_good` | `assumed_failed` | null (awaiting). Replaces the pool-only
  `unreported` marker from the review branch.
- `POST /jobs/{id}/results` `{results: [{project_item_id, quantity_failed}]}`: report or correct from History (same
  recording and requeue as plate clear). `GET /printers/{id}/plate-results` adds `print_failed`.

## Tests
Per cell of the table above, for a fixed and a pool group; assumed results not counting toward waivers / holds; the pause
(threshold, reset by a good print, off at 0, reason cleared on resume); late report and correction from History; settings
round trip; migration.
