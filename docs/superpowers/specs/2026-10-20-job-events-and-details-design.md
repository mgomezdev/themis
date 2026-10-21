# Job events + generic job-details accessor (design)

Status: implemented (see the plan's task list); notes at the end. Builds on `docs/events.md` (BIZ-249/269).

## Goal

Printer plugins report what happened to a print; core publishes a notice naming the job; any interested plugin reacts by asking core
for the job details it cares about. Example: "bambu: Print job complete: job `<id>`" -> the filament plugin calls
`get_details(<id>, ["filament_usage"])` and decrements the library.

## Decisions

1. **Core owns the job ID and the details accessor.** Plugins never mint IDs.
2. **Job ID stays the existing int `jobs.id`** (64-bit; no practical exhaustion). No `uid`, no PK swap.
3. **Plugins talk to core through a callback**; plugins never call `hub.publish` for core events (unchanged rule, and a disabled
   plugin's callback is rejected like a disabled publisher). Core publishes.
4. **Events carry the originating plugin** in a new `origin` envelope field.
5. **One generic accessor**, `get_details(job_id, fields)`, open field vocabulary.

## 1. Job ID

- **No new ID.** The cross-plugin job ID is the existing `jobs.id`. SQLite INTEGER PKs are signed 64-bit (~9.2e18), so exhaustion
  is not a practical concern. No migration, no `uid`, no in-flight-event compatibility issue; `entities.job_id`, `dedup_key`
  (`job.complete:<id>`) and every idempotency key are unchanged.
- Rule: `jobs.id` is never reused or recycled (keep the PK `AUTOINCREMENT` semantics; a deleted job's id is not handed out again),
  so an id in an old event can only ever mean that job. Test below.

## 2. Plugin -> core callback, event origin

- **Injection.** `report_job_event` is not a plain `PluginContext` value. Core builds a per-plugin `JobReporter` (bound to the
  plugin's `plugin_id`, a session factory and the hub) in `app/plugins/host.py` and passes it via `PluginContext.job_reporter`.
  Plugin code imports nothing from `queue_engine`; the reporter lives in core and delegates to it.
- Signature: `await ctx.job_reporter.report(job_id, kind, details=None)`; `kind` in `started | complete | failed | blocked`.
  (`cancelled` is **not** added now: cancel is core-initiated and stays `job.failed`/existing flow; revisit if a vendor needs it.)
- **Mid-print `blocked` (optional, printer-initiated).** A printer may report `blocked` for a job that is `printing` (filament runout,
  hardware fault, ...). Nothing requires it; core must tolerate it and never depend on it. Semantics: a **notice only, no state change**.
  The job stays `printing` and keeps its printer: the queue treats `blocked` as "released, re-evaluate", so flipping the status would let
  another printer claim a job whose physical print is only paused. The reason is stored as detail field `blocked_reason` (origin = the
  printer plugin) and published as best-effort `job.blocked` with payload `{reason, phase: "print"}` (queue-initiated blocks carry
  `phase: "queue"`). Repeats of the same reason for the same job while it is still `printing` publish once. There is no `resumed` kind:
  the print ends via the usual `complete` / `failed`, and a stale `blocked_reason` is deleted when they commit. Existing
  recovery (`_reconcile_printing_jobs` failing a job the printer ended) is unchanged. `blocked` for a job in any other state follows the
  queue rules above.
- Core validates the job's printer belongs to `plugin_id`, applies the state transition (existing atomic claim; the loser returns
  without publishing), persists `details` in the **same transaction**, then publishes.
- **Durability follows the event class** (`docs/events.md` decision 1): `started`/`complete` -> `enqueue_durable` in that
  transaction + `hub.wake()` after commit; `failed`/`blocked` -> best-effort `hub.publish` after commit. No new durable class.
- **Envelope `origin`**: optional string, the reporting plugin id, or `core` when core acts itself (manual completion). Validated:
  must be `core` or an installed plugin id, and a plugin's reporter can only set its own id (it cannot claim `core`). `source` stays
  `core`. Adding a field to a frozen `extra=forbid` envelope: `schema_version` of the affected core classes bumps 1 -> 2, the
  docs/events.md envelope table gains `origin`, and stored v1 envelopes deserialize with `origin = None` (default), so pending durable
  rows keep working. `event_outbox` stores the envelope JSON, so no column/migration is needed for `origin`.
- Display: `job_complete.notices` (queue source only) prepends the origin: `bambu: Print job complete: job <id>`. `None` origin
  renders as today.

## 2a. Release rule: after the print starts, only the user releases a job from its printer

The boundary is the **start of the physical print**: the commit that sets `printing` (print command accepted by the printer) and writes
`job.started`. Slicing, `sliced` and `uploading` are pre-start; the queue may still release and rescue a job there exactly as today
(slice failure -> `blocked` + rescue on another printer, upload/start error -> `failed`, lost cache/gcode -> back to `queued`, restart
reset). Nothing about those paths changes.

From `printing` on, `jobs.assigned_printer_id` is cleared **only by a user action** (cancel, requeue/edit, stop or delete the printer).
Printers, plugins and the queue engine never release it, whatever they report; a reporter call can change status and publish events but
never touches the assignment.

- A print the printer ends in failure (physical cancel) becomes `failed` but **keeps** the printer as history (today `~590` clears it).
  `complete` already keeps it. Terminal jobs do not reserve the printer; `awaiting_plate_clear` still gates new work.
- **Exception: a dead printer.** If a printer stays offline longer than the user's `printer_dead_after_minutes` setting, it can no longer
  hold its job: core fails the `printing` job (best-effort `job.failed`, `origin="core"`, `phase: "print"`) and requeues it at the top of
  the queue, releasing the assignment. A printer that is offline for less than the cutoff is "undead": it still counts in queue
  processing and nothing is triggered when it returns. This is a core action, not a printer or plugin one, so the rule above still
  holds for everything a printer can report. `awaiting_plate_clear` stays set on the dead printer.
- Mid-print `blocked` from a printer (§2) already obeys this: no status change, no release.
- "No eligible printer" blocking (queue-initiated) applies to unassigned jobs only; it never touches a started job.
- Pre-start behaviour, including automatic rescue of a slice-failed job, is unchanged, so the CLAUDE.md "Slicing failure recovery"
  invariant stays as is.

## 3. `get_details(job_id, fields)` and field declarations

```python
async def get_details(job_id: int, fields: Sequence[str], origin: str | None = None) -> dict[str, DetailResult]
# DetailResult = {"status": "ok" | "unsupported" | "unavailable" | "unknown_field", "value": Any, "origin": str | None, "updated_at": str | None}
```

- Core-internal function, also handed to plugins as `PluginContext.job_details` (read-only). Core-owned.
- **Field names and value shapes are not enforced by code.** Core has no field registry or schema validation; any string is a valid
  field and any JSON value is stored. Meaning is a documented contract, not a runtime check.
- **Statuses** (a bad field never fails the call, nothing raises):
  - `ok`: a value was reported.
  - `unsupported`: the source plugin says it cannot provide this field (e.g. a printer that cannot report actual filament used).
    Permanent for that source/model; consumers fall back or skip.
  - `unavailable`: the source declares the field but reported no value for this job (yet or this time).
  - `unknown_field`: no installed source declares the field and nothing stored.
- **Declaring fields (at install/load).** A plugin lists what it produces in its manifest, like `defines_events`:
  `PluginManifest.job_fields=(JobFieldDef("print_start_time", description=…, type_hint="iso8601"|"seconds"|"grams"|…), …)` and
  `unsupported_job_fields=("filament_used_grams",)` for standard fields it knows it cannot provide. Core builds a **field catalog**
  (field -> sources, with description/type hint/supported) from enabled plugins, exposed read-only at `GET /api/v1/job-fields`. Type
  hints are documentation for consumers, not validation. A plugin may also mark a field unsupported at run time for one job
  (`reporter.report(..., details={"filament_used_grams": UNSUPPORTED})`), for model-dependent gaps; stored as `status=unsupported`.
- **Minimum set for printer plugins** (defined in `docs/printer-interface.md`, not in code): `print_start_time`, `print_end_time`,
  `result` (`success` | `failure`), and the filament/time fields the plugin can supply, each either declared in `job_fields` or in
  `unsupported_job_fields`. Conformance is checked, not enforced: the plugin test kit gains a manifest check, and the host logs a
  warning at load for a printer plugin that neither declares nor marks-unsupported a minimum field. A warning never blocks the plugin.
- **Single writer per field (rule, not enforced yet).** A job's field is meant to have exactly one writing plugin; two plugins
  modifying the same field on one job is a contract violation. Core does **not** enforce it for now (storage keeps
  `(job_id, field, origin)` so nothing is lost if it happens). Enforcement (e.g. first-writer-owns, manifest ownership check,
  rejecting or warning on a second origin) is deferred; tracked under "Out of scope". The multi-origin resolution below is only an
  interim fallback for that case.
- **Resolution for one field**: stored value (any origin; with `origin=` only that origin) -> else the job's printer plugin
  manifest: listed in `unsupported_job_fields` -> `unsupported`; declared in `job_fields` -> `unavailable` -> else the catalog:
  nobody declares it -> `unknown_field`. If several origins stored the same field and no `origin` is given, the printer-plugin origin wins,
  then latest `updated_at`.
- **Other plugins add fields to a job** (e.g. the filament plugin writes `filament_cost`) through the same reporter-style writer
  (`PluginContext.job_details_writer.set(job_id, {...})`), and declare them in their own `job_fields`. Convention (not enforced):
  non-printer plugins prefix names with their plugin id (`filament.cost`) to avoid collisions.
- **Storage**: table `job_details(job_id FK jobs.id, field, origin, status, value JSON, updated_at)`, unique `(job_id, field, origin)`
  (migration `v054`). Printer-plugin writes go through the reporter and commit with the state change; other writers commit with their own
  transaction. Survives the printer forgetting the job. Core may fill computed fields (e.g. `time` from job timestamps) via resolvers;
  a stored value wins over a resolver.
- **REST (debug, read-only)**: `GET /api/v1/jobs/{job_id}/detail-fields?fields=a,b` (distinct from the existing
  `GET /jobs/{id}/details`, which keeps its shape) and `GET /api/v1/job-fields`. New routes -> `openapi.json` regen +
  `contract.test.ts` URL check; no `response-keys.json` entry unless the frontend reads them.

## 4. Consumer flow (filament library) and compatibility

1. Hub delivers `job.complete` (durable) with `origin` and `entities.job_id`.
2. Handler calls `get_details(job_id, ["filament_usage"])`. Because details are persisted **in the same transaction** as the event,
   `unsupported` (plugin cannot report it) and `unavailable` (plugin did not report it) are real answers, not races: the handler falls
   back (below), else records "no usage" and acks; it does not rely on retry-until-present.
3. **Compatibility window.** The inventory handlers accept, in order: `get_details` result -> payload `loaded_filaments`/`actual_grams`
   (current shape) -> legacy `inventory`/`deduction_notes` (already honored). Core keeps writing the current payload fields for one
   release so in-flight durable events and `job_started.inventory` (which reads `loaded_filaments` from `job.started`) keep
   working; the payload fields are removed in a follow-up once no pending deliveries predate the change.

## Out of scope

Enforcing single-writer-per-field (see §3; deferred, to be designed later), replay/log UI (`docs/event-diagnostics-decision.md`, BIZ-270), new durability classes, a `job.cancelled` event, PK swap, cross-instance
ID federation.

## Tests (each must fail without the change)

- Reporter: a plugin reporting on another plugin's printer's job is rejected, and the job row and outbox are unchanged.
- Duplicate `report(complete)` calls leave exactly **one** `event_outbox` row and one deduction (assert rows, not "didn't raise").
- A plugin cannot set `origin="core"`; a `core`-acting manual completion carries `origin="core"`.
- A stored v1 envelope without `origin` deserializes with `origin=None` and is delivered normally.
- A deleted job's id is never reissued to a new job (insert, delete, insert; assert distinct ids).
- `get_details`: a mixed request returns `ok`/`unsupported`/`unavailable`/`unknown_field` per field (one case each, asserting the status and that nothing raises);
  a field in the plugin's `unsupported_job_fields` returns `unsupported` even when no row exists; a runtime `UNSUPPORTED` marker is stored and returned;
  an arbitrary field name with an arbitrary JSON value is stored and read back (nothing enforces names); a detail row is still readable after the delivery is
  `delivered` and after the printer client reports the job unknown.
- Details and state change roll back together on a forced failure (no `complete` row without its details).
- Inventory handler fallback order: details present, payload only, legacy only (three cases, each asserting the deducted grams).
- Field catalog: enabling a plugin adds its `job_fields` to `GET /job-fields`; disabling removes them; a printer plugin missing a minimum field
  (neither declared nor unsupported) logs a load warning and still loads.
- Interim fallback only (single-writer is not enforced): two origins storing one field: default read prefers the printer origin; `origin=` selects the other.
- Boundary tests (`test_vendor_extraction_boundary`, `test_provider_boundary`, `test_no_provider_in_core`) pass with the reporter in
  core and no vendor names added.


## Implementation notes (as built)

- `report(job_id, kind, details=None, *, reason=None)`; `started` is accepted only from `slicing`/`sliced`/`uploading`, `complete`/`failed` only from `printing`, and a mid-print `blocked` only
  for a `printing` job; anything else returns False without writing. The core adapters (`handle_print_complete`, the reconcile failure branch, the queue's own start path) use the same
  engine methods with the printer's `plugin_id` as origin, so bundled plugins need no change.
- Core writes `print_start_time` at the start commit and `print_end_time`, `result`, `print_seconds`, `filament_usage` (derived from the sliced job) at the end, as the printer plugin's origin; a
  plugin's own `details` win. `failure_reason` and a dead printer's `last_failure` are core's.
- Queue-initiated blocks publish `job.blocked` from one place (`_block_job`, plus the slice-failure and cache-gate paths) with `phase: "queue"`; only a fleet-wide no-match block (and a printer-reported one)
  also fires the webhook/notification. The no-match sweep runs at the end of each `_process_queue` and releases only blocks whose reason starts with `NO_MATCH_PREFIX`.
- The no-match sweep counts every printer except a dead one (queue-off, disabled, dormant and not-yet-registered printers count), syncs model targets first, and updates a reworded reason without a second announcement. `report(..., printer_id=)` refuses a report for a job on another printer. The v054 rewrite edits `jobs`' stored CREATE statement instead of rebuilding the table, because dropping it with foreign keys on cascades into every child table.
- Known limitation: a job requeued after its printer died keeps its earlier spool start snapshot, so the partial print's filament is not deducted when the reprint completes.
