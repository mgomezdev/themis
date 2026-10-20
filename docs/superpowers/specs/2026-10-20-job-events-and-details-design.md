# Job events + generic job-details accessor (design)

Status: draft for review. Builds on `docs/events.md` (BIZ-249/269).

## Goal

Printer plugins report what happened to a print; core publishes a notice naming the job; any interested plugin reacts by asking core
for the job details it cares about. Example: "bambu: Print job complete: job `<id>`" -> the filament plugin calls
`get_details(<id>, ["filament_usage"])` and decrements the library.

## Decisions

1. **Core owns the job ID and the details accessor.** Core assigns the ID; plugins never mint one.
2. **Job ID is a long opaque string** (UUID-capable, TEXT), not an int. Applies to `entities.job_id`, the accessor, and events.
3. **Plugins talk to core through a callback**, they do not publish core events. Core publishes the hub event.
4. **Events carry the originating plugin** (e.g. `bambu`), so subscribers and notices can show where it came from.
5. **Details are fetched through one generic accessor**, `get_details(job_id, fields)`, with an open-ended field vocabulary.

## 1. Job ID

- New canonical ID: UUID string (`uuid4().hex` or canonical form; pick one and validate on input).
- **Migration cost is the main risk**: `jobs.id` is an int PK referenced by FKs (`job_printer_configs`, orders, snapshots, pending
  writes, outbox `entities`), API routes, the frontend, `openapi.json` and `contracts/response-keys.json`.
- Recommended path: add `jobs.uid TEXT UNIQUE NOT NULL` (backfilled), make it the cross-plugin/event/API identifier, and keep the
  int PK as an internal rowid until a later migration retires it. Open question for the owner: is a full PK swap wanted now, or is
  the `uid` column enough? (SQLite INTEGER is 64-bit, so exhaustion is not the technical driver; opacity and uniqueness across
  instances are.)
- `EventEnvelope.entities["job_id"]` becomes `str`; dedup keys become `job.complete:<uid>`.

## 2. Plugin -> core callback, event origin

- `PluginContext` gains a callback, e.g. `report_job_event(job_id, kind, details=None)`, `kind` in
  `started | complete | failed | cancelled | blocked`. Core validates the job/printer belongs to the calling plugin, applies the
  state transition (existing atomic claim), persists any `details`, then enqueues the durable event in the same transaction.
- Envelope gains an `origin` field = reporting plugin id (`bambu`, `snapmaker`, ...; `core` when core itself acts, e.g. manual
  completion). `source` stays `core` (ownership rule in `docs/events.md` is unchanged).
- Human-readable form prepends the origin: `bambu: Print job complete: job <id>`. Notification channels use that form.
- Payload stays minimal; no filament data in the payload any more (the existing `loaded_filaments` / `actual_grams` snapshot moves
  behind the accessor, with a compatibility window for the inventory handler).

## 3. `get_details(job_id, fields)`

```python
async def get_details(job_id: str, fields: Sequence[str]) -> dict[str, DetailResult]
# DetailResult = {"status": "ok" | "unavailable" | "unknown_field", "value": Any, "origin": str | None, "updated_at": str | None}
```

- Core-owned. Per-field result so one missing field never fails the call; unknown names return `unknown_field`, never raise.
- **Storage**: `job_details(job_id, field, value JSON, origin, updated_at)`, unique on `(job_id, field)`. Plugins write via the
  `report_job_event` details argument or a `report_job_details(job_id, {field: value})` callback; core persists, so details survive
  the printer forgetting the job.
- **Open vocabulary**: any field name is storable. Core documents a small registry of well-known fields with schemas
  (`filament_usage`, `time`, `loaded_filaments`, ...) and validates only those; other fields are stored as given. Consumers
  declare which they need.
- Core may also compute fields from its own tables (e.g. `time` from job timestamps) via registered resolvers; stored values from
  plugins win unless the resolver is marked authoritative.
- Exposed to plugins through `PluginContext` (no vendor imports in core; capability-boundary tests still apply) and to the REST
  API read-only for debugging.

## 4. Consumer flow (filament library)

1. Hub delivers `job.complete` (durable, at-least-once) with `origin`, `entities.job_id`.
2. Handler calls `get_details(job_id, ["filament_usage"])`.
3. `status != ok` -> retry later / skip per existing inventory rules; `ok` -> plan deduction, idempotent on `job_id` as today.

## Out of scope

Replay/log UI (see `docs/event-diagnostics-decision.md`), changing durability classes, cross-instance federation of IDs.

## Tests (planned)

- Callback rejects a plugin reporting on another plugin's job; duplicate reports yield one event (dedup key).
- Envelope carries `origin`; notice text prepends it.
- `get_details`: ok / unavailable / unknown_field per field, mixed request, persisted detail survives after the event is handled.
- Migration backfills `uid`; old int-ID references still resolve during the compatibility window.
- Boundary tests (`test_vendor_extraction_boundary`, `test_provider_boundary`) stay green.
