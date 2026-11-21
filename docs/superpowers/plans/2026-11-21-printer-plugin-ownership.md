# BIZ-391: printer plugin ownership

The initial supported manufacturer integrations remain Bambu, Elegoo Centauri Carbon,
and Snapmaker U1. Keep their existing plugin IDs, model UUIDs and protocol behavior.
The existing `moonraker` ID becomes the manufacturer-neutral generic Klipper plugin;
remove Voron/Sovol declarations rather than adding unowned manufacturer integrations.
Historical registry rows and configured printers are preserved, never rebound.

A printer row has one immutable plugin binding. Reserve its configured host across
plugins/models/ports, and its serial number across addresses. Compare host case,
whitespace and trailing DNS dots; compare serial case and whitespace. No DNS lookup
or hardware writes are needed. Aliases with no shared serial cannot be inferred;
multiple independent printers behind one hostname are outside the initial fleet.

Implement vendor-neutral ownership helpers and SQLite INSERT/UPDATE triggers in v081
for atomic enforcement, including concurrent writers. API create/edit return 409;
imports skip conflicting entries with warnings. Allow metadata edits/deletion of
historical conflicts, but block automatic and manual connections until reconciled.
No queue, plate-clear, filament or transport semantics change.

Verify generic-only model catalog, persisted custom dimensions/toolheads, exclusive
ownership across plugins/models, disabled bindings, edit atomicity, import batches,
concurrent database claims and preservation of historical conflicts. Run backend
suite and Ruff. Regenerate OpenAPI for explicit plugin-change rejection.

The manufacturer compliance epics BIZ-327/328/329 remain open: their hardware and
protocol gaps require separate implementation and recorded hardware verification.
