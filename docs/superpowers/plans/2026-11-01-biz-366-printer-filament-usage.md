# BIZ-366: printer consumption and AMS rollover accounting

Related BIZ-235 work is uncommitted in the current checkout. Preserve it; this enhancement builds on its claim-time filament resolution.

## Initial backend milestone

- Add a versioned, validated cumulative usage report through `JobReporter.report_usage`, bound to the physical print attempt and printer. Ignore duplicate/out-of-order revisions.
- Save generic start-time routing and loaded-slot facts in the same transaction as `job.started`; inventory interprets provider/spool bindings from those immutable facts.
- Snapshot all bound loaded spools as possible AMS rollover replacements. Provider calls remain in the inventory observer, outside the queue loop.
- Normalize logical slicer filament indices for AMS versus already-remapped physical tools. Keep AMS slicer profiles in model order and dispatch the complete ordered vendor tray-id list.
- Prefer final complete printer reports; retain uncertainty for partial/invalid/wrong-attempt/ambiguous rollover reports. Freeze the chosen completion allocation before outbox writes, preserving retry idempotency and provider namespace.
- Keep old jobs without a start context on the existing compatibility path. Expose normalized results as a generic `filament_accounting` job detail.

## Remaining work in the issue

- Verify actual Bambu/AMS and other vendor consumption and rollover fields against authoritative protocol evidence and real devices. The current Bambu adapter parses tray_now/loaded trays; quantified per-slot consumption is not implemented or verified. A normal active-tray change is not itself rollover evidence.
- Wire verified vendor reports into the new reporter. If only unquantified rollover is known, submit an incomplete report and require reconciliation rather than assigning all usage to the original spool.
- Same physical slot refilled with another spool during one print requires binding epochs and consumption checkpoints; v1 deliberately cannot claim a complete attribution for that case.
- Add explicit telemetry support declarations, protocol fixtures/opt-in hardware checks, and a reconciliation workflow for pending allocations / late final reports. The first completion allocation is frozen; revisions after completion do not automatically rewrite previous inventory writes.
- Confirm multi-unit AMS tray mapping and measured usage on physical devices. Slice totals remain estimates until a plugin supplies verified telemetry.

- Make the starting-weight/outbox lifecycle per physical attempt explicit for a job requeued after a failed physical print; existing inventory uniqueness is job/provider/spool-scoped, while v1 telemetry is attempt-bound. Do not claim repeated physical attempts of one job are fully reconciled by this foundation.
