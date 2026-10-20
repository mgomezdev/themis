# Job events + generic job-details accessor (design)

Status: draft for review. Builds on `docs/events.md` (BIZ-249/269).

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

## 3. `get_details(job_id, fields)`

```python
async def get_details(job_id: int, fields: Sequence[str]) -> dict[str, DetailResult]
# DetailResult = {"status": "ok" | "unavailable" | "unknown_field", "value": Any, "origin": str | None, "updated_at": str | None}
```

- Core-internal function, also handed to plugins as `PluginContext.job_details` (read-only). Core-owned.
- Per-field result: a missing field never fails the call; unknown names return `unknown_field`, never raise.
- **Storage**: table `job_details(job_id FK jobs.id, field, value JSON, origin, updated_at)`, unique `(job_id, field)` (migration
  `v054`). Written only through the reporter (`report(..., details=...)` or `reporter.set_details(job_id, {...})`) so it commits
  with the state change. Survives the printer forgetting the job.
- **Vocabulary**: any field name is storable. A small registry of well-known fields has schemas and is validated
  (`filament_usage`, `time`, `loaded_filaments`, `actual_grams`); others are stored as given. Computed fields (e.g. `time` from job
  timestamps) come from resolvers; **tie-break**: a stored plugin value beats a resolver unless the field is registered
  `authoritative=core`; for two writes to one `(job, field)`, last `updated_at` wins, ties by higher row version.
- **REST (debug, read-only)**: `GET /api/v1/jobs/{job_id}/detail-fields?fields=a,b` (distinct from the existing
  `GET /jobs/{id}/details`, which keeps its shape). New route -> `openapi.json` regen + `contract.test.ts` URL check; a new
  `response-keys.json` entry only if the frontend reads it (it does not initially, so none).

## 4. Consumer flow (filament library) and compatibility

1. Hub delivers `job.complete` (durable) with `origin` and `entities.job_id`.
2. Handler calls `get_details(job_id, ["filament_usage"])`. Because details are persisted **in the same transaction** as the event,
   `status != ok` is a real gap (plugin did not report usage), not a race: the handler falls back (below), else records "no usage"
   and acks; it does not rely on retry-until-present.
3. **Compatibility window.** The inventory handlers accept, in order: `get_details` result -> payload `loaded_filaments`/`actual_grams`
   (current shape) -> legacy `inventory`/`deduction_notes` (already honored). Core keeps writing the current payload fields for one
   release so in-flight durable events and `job_started.inventory` (which reads `loaded_filaments` from `job.started`) keep
   working; the payload fields are removed in a follow-up once no pending deliveries predate the change.

## Out of scope

Replay/log UI (`docs/event-diagnostics-decision.md`, BIZ-270), new durability classes, a `job.cancelled` event, PK swap, cross-instance
ID federation.

## Tests (each must fail without the change)

- Reporter: a plugin reporting on another plugin's printer's job is rejected, and the job row and outbox are unchanged.
- Duplicate `report(complete)` calls leave exactly **one** `event_outbox` row and one deduction (assert rows, not "didn't raise").
- A plugin cannot set `origin="core"`; a `core`-acting manual completion carries `origin="core"`.
- A stored v1 envelope without `origin` deserializes with `origin=None` and is delivered normally.
- A deleted job's id is never reissued to a new job (insert, delete, insert; assert distinct ids).
- `get_details`: mixed request returns `ok`/`unavailable`/`unknown_field` per field; a detail row is still readable after the delivery is
  `delivered` and after the printer client reports the job unknown.
- Details and state change roll back together on a forced failure (no `complete` row without its details).
- Inventory handler fallback order: details present, payload only, legacy only (three cases, each asserting the deducted grams).
- Boundary tests (`test_vendor_extraction_boundary`, `test_provider_boundary`, `test_no_provider_in_core`) pass with the reporter in
  core and no vendor names added.
