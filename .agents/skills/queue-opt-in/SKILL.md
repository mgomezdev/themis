---
name: queue-opt-in
description: Check whether Themis sends queued prints only to queue-enabled printers. Use when changing queue eligibility, slice resumption, uploads, or printer queue settings.
---

# Queue opt-in

Every queued start command requires the target printer's committed `queue_on` flag to be true. A printer switched off during upload must keep the slice for another attempt.

From `backend/` with `pip install -e '.[dev]'`:

```sh
python -m pytest -q tests/services/test_print_start_invariants.py -k 'parked_slice or end_of_upload'
```

The checker enumerates both values of queue opt-in and plate clearance for a parked slice and all four combinations of changes to those flags during an upload. It runs the real queue and the printer settings route against file-backed SQLite and observes calls to a simulated printer. It assumes one queue process and one printer. It does not exercise firmware, topology changes, or starts made outside Themis. The direct-print control is in the `print-plate-clearance` skill.

At `053dfa68b834e0dbcdbfbcecd4e2b544a4788a9f`, an opted-out printer started a parked slice, and switching off the queue during upload still sent the command. For a mutation check, remove the `Printer.queue_on` predicate from `print_start.reserve_plate` in a scratch copy. The queue-off case must fail at the start-call assertion. The case with both flags changed stays blocked by the plate predicate. Restore the predicate and rerun.

## Covered code

- `backend/app/services/queue_engine.py` — `_process_queue` checks the flag before resuming a parked slice.
- `backend/app/services/queue/printing.py` — `_upload_and_start_current` checks permission after upload and parks the same slice if the start is deferred. `_pause_failing_printer` uses the start lock when switching the queue off.
- `backend/app/services/print_start.py` — `reserve_plate` requires queue opt-in in the same transaction that reserves the plate.
- `backend/app/api/routes/printers.py` — `update_printer` serializes queue-setting writes with command submission.

Last checked against code commit `2cfa5f167493d62611bd4a9dace96bbabb98fc07`.

After changes to these paths, rerun the checker and the mutation. Use pstack's `maintain-formal-verification-skill` if the check has drifted. Advance the sync commit only after checking that the cases still represent the code.
