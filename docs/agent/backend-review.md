# Backend Review Guide

Checklist for reviewing a backend change (or the backend half of a cross-cutting one) before it's
considered done. Read `backend.md`, `data-model.md`, and `conventions.md` first if you haven't — this
doc assumes that context and doesn't repeat it. These are systemic gotchas specific to this codebase's
shape, not generic advice.

## 1. Filament identity — know which ID space you're in

This codebase has more "filament identifiers" than it looks like at first, and they are not
interchangeable:

- `job_printer_configs.filament_id` — a Spoolman **filament** (catalog spec) ID.
- `printer.loaded_filaments[slot].spoolman_spool_id` — a Spoolman **spool** (physical instance) ID.
  Not the same thing as the above, despite the similar name.
- `job_printer_configs.filament_type`/`filament_color` — the eligibility **ask**, matched against a
  printer's loaded slots. Not what actually gets used for slicing.
- `filament_profile` (on a config, or a loaded slot) — the **OrcaSlicer preset name**, a string, used
  for slicing. Legacy fallback on the config; the real source is the matched slot's own value.
- `printer.loaded_filaments[slot].filament_id` — for Bambu, this is the **AMS tray code**, not a
  Spoolman ID at all.
- `tool_index` vs `filament_map` — two different addressing schemes for multi-material jobs (single
  tool selection vs. a per-model-filament→tool mapping list). Don't assume one implies the other.

Before writing code that reads or compares any of these, confirm which one you actually have. Before
reviewing code that touches filament matching, spool binding, or multi-material routing, confirm the
author didn't conflate two of the above.

## 2. Contract fidelity with the frontend

This codebase has no schema-sharing or codegen between backend and frontend — every shared contract
(API response field names, the `SCOPES` registry mirrored by hand in `frontend/src/api/apiKeys.ts`,
`Sidebar.tsx`'s hand-duplicated settings nav) is kept in sync manually, which means it can drift
silently and both sides' own tests can still pass (each side tests against what it assumes the other
does, not what the other actually does).

If a change adds or renames a response field the frontend consumes, grep the frontend for where it's
read and confirm the key matches byte-for-byte — don't trust a plan, a negotiated contract, or "I
already checked this." Same in reverse for a new required frontend-side field. If you touch
`app/auth.py`'s `SCOPES` or add a Settings sub-page, check the two known hand-mirrored spots
(`api/apiKeys.ts`, `Sidebar.tsx`'s `settingsSubItems`) for drift.

## 3. Async / queue-loop safety

`queue_engine.py`'s `_process_queue` loop reconciles running jobs before claiming new ones each
iteration. Anything awaited synchronously on that path — including several calls deep — adds its
latency to every iteration. Real external I/O (HTTP, SMTP, printer control calls) must not be awaited
directly there.

The established pattern for this is `webhook_service.schedule()`: do the fast synchronous part (load
config, build payload), then hand the actual delivery to `asyncio.create_task(...)` instead of
awaiting it. Any new fire-and-forget delivery mechanism should follow the same shape.

## 4. Session & DI discipline

Every DB read/write in a request handler goes through `Depends(get_session)` — never a raw import of
`SessionLocal`/`engine` from `database.py`. Code that imports the module-level session factory
directly bypasses the test suite's in-memory-DB override and silently depends on whatever's actually in
`<repo-root>/data/themis.db`. `printer_manager` and `thumbnail_regen.py` already do this (a known,
standing gap, not something to fix opportunistically) — don't copy the pattern into new code.

## 5. Migrations

New table → fine to ride on `Base.metadata.create_all`. New column on an *existing* table → needs its
own migration file (`backend/app/migrations/v0NN_name.py`, registered in `runner.py`) — there is no
other mechanism. See `data-model.md` § Migrations for the exact steps. If the table/column belongs to a
migration that hasn't shipped anywhere yet (still in an unreleased branch), amend it in place instead
of adding a new one for a field that should've been there from the start.

Pydantic's plain `BaseModel` silently drops unknown fields on write, with no error — so a field the
frontend sends but that has no matching column, and no line in the route assigning it, disappears
without a trace instead of failing loudly. When reviewing a new field on a request/response model,
confirm it has a matching column *and* that the route actually assigns it in both directions.

## 6. Query efficiency on polled endpoints

`/api/v1/queue` and `/api/v1/fleet` are polled frequently by the frontend. A per-row `session.get(...)`
or `session.execute(select(...))` inside a loop over jobs/configs/printers on either of these routes is
worth batching (`select(Model).where(Model.id.in_(ids))`, build a dict, look up from that) rather than
leaving as N+1 — the cost compounds with poll frequency in a way it doesn't on a one-shot endpoint.

## 7. Secrets

No masking convention exists in this codebase yet — `webhook_config.secret`, `spoolman_config.api_key`,
and similar fields all round-trip in cleartext on GET. That's a known, standing gap. Match the existing
(unmasked) convention for a new secret field rather than half-fixing it by masking just the new one;
flag the broader gap separately if you think it should change.

## 8. Existing invariants

Don't violate anything in `conventions.md` § Invariants (blocked vs failed, `awaiting_plate_clear`,
`filament_profile` vs the ask, per-printer flags read from the client not `StartPrintOptions`,
cancel↔stop, head-of-line queue, mandatory auth scopes). Re-read the relevant invariant before writing
code that touches one of these areas, not just before review.

## 9. Tests

TDD: a test that failed for the right reason before the fix, for every behavior change — not just
coverage added after the fact. Full suite green (`pytest -v` from `backend/`) before calling anything
done.
