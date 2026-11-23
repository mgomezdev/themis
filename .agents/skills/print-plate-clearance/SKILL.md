---
name: print-plate-clearance
description: Check whether Themis requires a clear plate for queue and direct starts, including interrupted starts. Use when changing printer start, completion, queue recovery, or operator clearance.
---

# Print plate clearance

Themis sends a fresh print command only to a clear plate. Once it submits a command, only a valid operator clearance can release that plate. A second start can print over the first part.

From `backend/` with `pip install -e '.[dev]'`:

```sh
python -m pytest -q tests/services/test_print_start_invariants.py
```

The checker crosses the clearance and queue flags for a parked slice, applies each combination of flag changes during upload, and enumerates restart cuts during upload, at an unconfirmed start, after acceptance, before the job-start commit, and after that commit. It varies whether the device finishes before or after restart when acceptance is known. It also exercises both direct-start routes with either plate flag, a queued job waiting behind a direct upload, clearance during command submission (including repeated request cancellation), and clearance while the device reports busy. The harness runs the actual backend with a file-backed SQLite database and a simulated printer. A restart discards memory and creates a new manager and queue against the same database.

The bound is one printer, at most two jobs, and one restart per case. It assumes one Themis process, durable SQLite commits, accurate idle telemetry, and that an operator who confirms an idle plate has inspected it. Firmware, independent device commands, and storage failures during a commit are outside the check.

At `053dfa68b834e0dbcdbfbcecd4e2b544a4788a9f`, restarting after acceptance and before the job transaction committed let a second job start on the same plate. For a mutation check, change `reserve_plate` in a scratch copy so it writes `awaiting_plate_clear=False`. Run with `-k after-acceptance`. Both schedules must fail at the second start assertion. Restore the write and rerun the checker. If a fault hook no longer meets its boundary, update the harness. If a forbidden command reaches the simulated printer, replay the counterexample in the code.

## Covered code

- `backend/app/services/print_start.py` — `reserve_plate` commits the gate before `send` submits the command.
- `backend/app/services/queue/printing.py` — `_upload_and_start_current`, `mark_printing`, and `_reconcile_printing_jobs` cover automatic starts and idle recovery.
- `backend/app/services/queue_engine.py` — `start` recovers interrupted uploads, and `_process_queue` rescans parked slices.
- `backend/app/services/printer_manager.py` — `load_awaiting_plate_clear_from_db` restores the gate, and `is_printer_ready` applies it to the queue.
- `backend/app/api/routes/printer_files.py` and `backend/app/api/routes/printer_plate.py` — `_direct_start` shares the reservation, and `plate_cleared` serializes clearance with it.

Last checked against code commit `2cfa5f167493d62611bd4a9dace96bbabb98fc07`.

After changes to these paths, rerun the checker and the mutation. Use pstack's `maintain-formal-verification-skill` if the check has drifted. Advance the sync commit only after checking that the cases still represent the code.
