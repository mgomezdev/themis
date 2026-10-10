# BIZ-269 — migrate print-completion effects to reliable event subscribers

Epic BIZ-266; builds on BIZ-249 (`docs/events.md`, which now has a "First consumer: print completion" section).

## Approach
1. Keep the completion transaction authoritative (state, `awaiting_plate_clear`, gcode cleanup, the atomic claim). Add the durable
   `job.complete` event to it (`completion_events.enqueue_job_complete`) for both completion paths (queue + manual).
2. Resolve the spool at completion and carry it in the payload; subscribers never look at "current" printer state.
3. Maintenance accrual: `jobs.maintenance_accrued` (v046, backfilled) claimed in the same transaction as the counters.
4. Inventory deduction: skip planning when an outbox write for (provider, spool, job) exists (also in `complete_deferred`).
5. Notices (WebSocket/webhooks/notification channels): subscriber, `source=queue` only, each step independent.
6. Existing tests that asserted effects inline call `settle_events(factory)` (new helper) after the completion call.

## Tests (new: `tests/services/test_completion_events.py`)
One event per completion despite concurrent/duplicate callbacks and reconciliation; atomic handoff; crash before commit; effects owed
after commit/before handler; crash during handler (flag + counters roll back together, retry applies once); redelivery after a lost
acknowledgement (maintenance and inventory); spool resolved at completion; failing notices do not affect completion or the critical
effects; manual completion notifies nobody; payload validation; v046 migration. Mutation-checked: dropping the idempotency guards fails them.

## Not in scope
Publishing `job.failed` / `job.blocked`; moving webhooks/notification channels behind plugins (BIZ-252); project webhooks (BIZ-172).
