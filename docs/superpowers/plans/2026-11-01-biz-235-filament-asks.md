# BIZ-235: per-model filament asks

Use the existing provider-neutral tuple editor and claim-time slot planner. Keep current fleet-wide matching rules: skip an individual printer mismatch, block only when no compatible live candidate can satisfy the job.

1. Show a filament-map row for single-filament models on multi-tool printers, preserving legacy asks and pins when editing. Keep manual type/color entry independent of inventory.
2. Persist every candidate's failing model filament numbers with the fleet-wide block and expose them on job details. Keep complete printer-labelled block reasons. Clear failures on requeue, unblock and claim.
3. Preserve physical tool positions when constructing slicing presets, including empty tools. A per-model map takes priority over a legacy job-level pin. Keep resolved routing in cache inputs.
4. Verify empty placeholders, shared tools, single asks on T2/T3, cache changes, all-row failure reporting, another printer claiming the job, slot-sync wakeups, API edit/requeue and component editing. Add an idempotent deployed-schema migration.

Hardware verification: a real U1 slice/print remains a manual check; automated checks exercise the Snapmaker client's slice-mapping declaration and the producer/remap boundary.
