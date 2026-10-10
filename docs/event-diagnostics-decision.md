# Decision: event diagnostics, audit separation, replaceable log sinks (BIZ-270)

Status: **decided — no new component now; remote sinks explicitly deferred** (re-open triggers below). Context: `docs/events.md`
(BIZ-249) and print-completion consumers (BIZ-269) exist; operators already have `/api/v1/events/{catalog,subscribers,deliveries}`.

## Operational use cases (what an operator must be able to answer)

1. *Did job 42's completion reach inventory, maintenance and notifications?* — `GET /events/deliveries?entity=job_id:42&status=delivered` (and `pending` / `dead`): event identity + entity ids + per-subscriber outcome.
2. *Which subscriber is failing, since when, and why?* — subscriber, last redacted error, attempts, next retry.
3. *What is stuck?* — durable deliveries `pending` / `dead`, backlog size per subscriber.
4. *Is a subscriber falling behind or losing events?* — lane depth, `dropped`, `timed_out`, `skipped_inactive`.
5. *Retry or give up on one delivery* — `POST /events/deliveries/{id}/retry`.

Not a use case here: forensic history of every best-effort notice, full-text search, dashboards over event volume, replay.

## What must be queryable, and where it already is

| Need | Source | Bounded by |
|---|---|---|
| Event identity (`id`, `name`, `dedup_key`, `occurred_at`, `correlation_id`), entity ids | `event_outbox`, returned by `GET /events/deliveries` (each row carries `entities`; filter by `entity=job_id:42`, `event_id`, `dedup_key`, `name`, `subscriber`, `status`) | durable events only; purged 7 d (30 d with a `dead` delivery); at most 500 rows per call |
| Handler outcome, attempts, `last_error` (redacted), next retry | `event_deliveries` | one row per (event, subscriber) |
| Counters, lane depth, last error/ok, last event id | in-memory `hub.snapshot()` → `GET /events/subscribers` | per subscriber; reset on restart |
| Plugin handler health | the plugin's `state.last_error / last_ok_at` | one value per plugin |
| Everything, as text | the standard `app` logger (one WARNING per failed attempt — a durable delivery makes at most 8; drops are logged first, then every 100th) | log rotation of the deployment |

## Decision 1 — diagnostic store: use what exists

Standard structured logs remain the baseline, and the **durable outbox/deliveries tables are the bounded, queryable
delivery-diagnostic store** for durable events (the only class where history matters). We do **not** add a second event-log table or a
log UI. Best-effort events keep counters + log lines only: losing one costs a notice, so their per-event history is not worth storage.
If operators later need recent best-effort failures, the smallest step (a sketch, **not implemented**) is an in-memory ring (≤ 200 entries per subscriber) behind the
existing `/events/subscribers` response — no new table, no retention setting.

Specified limits (the first five are enforced today; the redaction rule covers failure outcomes, not every log line):

* **Retention:** outbox rows 7 days after creation (30 days if any delivery is `dead`), pending never purged; logs per deployment.
* **Volume:** durable classes are low-volume by definition (job completions); a warning is logged above 10 000 undelivered per subscriber.
  Best-effort lanes are bounded (default 1 000) and drop the newest.
* **Noisy-event sampling:** drop warnings log the first drop and then every 100th; no per-event sampling is needed because failures are
  logged once per failed delivery, and durable retries are capped (8 attempts, backoff to 15 min).
* **Size caps:** envelope payload ≤ 16 KiB; `last_error` ≤ 300 characters.
* **Redaction:** every handler *failure outcome* goes through `eventing.redaction.redact_error` (plugin secrets, URL credentials, `password=`/`token=`
  pairs) before it is logged, stored or returned, and `/events/deliveries` re-redacts at read time; raw exceptions are never persisted. (Unexpected internal errors still get a normal traceback in the log, as everywhere else in the app.) Payloads are notices keyed by ids, and no core
  event carries a secret; a plugin that puts PII in its own payload owns that choice, and the outbox stores it for at most the retention above.
* **Access control:** `settings:read` to view, `settings:write` to retry; the admin-only plugin install actions are unrelated.

## Decision 2 — security audit stays separate

`audit_log` (append-only, written in the same transaction as the admin action it records: plugin install/upgrade/rollback/uninstall,
restart; today there is no read API for it) is **not** merged with event diagnostics. Delivery diagnostics are purgeable operational data with a short retention, readable by
`settings:read`, and written by many subscribers; the audit log needs append-only semantics, an actor, long retention and tighter access (any future reader must be admin-session only, like the actions it records). We
have not demonstrated equivalence on access control, retention or tamper properties, so they remain two stores. Events never write audit rows;
audit rows are never delivered as events to sinks without a separate decision.

## Decision 3 — remote log sinks (syslog / Loki / Elastic / S3): out of scope now

If ever built (nothing below exists yet), a sink is an **additive fan-out target** (a `fan_out` capability, every enabled sink receives every diagnostic record
independently), **not** an exclusive replaceable provider: swapping one sink for another must not silently drop the others, and the
local logs/tables stay authoritative. It would receive only redacted, size-capped records (never raw payloads), and its failure or latency
could never block publication or another subscriber — the same containment as any subscriber (own lane, timeout, redacted error; a
sink is just a best-effort subscriber of an internal `diagnostic.recorded`-style stream, or of the logger).

**Deferred.** Nothing in the product needs it: Docker deployments already ship container logs to whatever collector the operator runs.
Re-open when any of these is true, and then open an implementation issue with these measurable tests: (a) a user must keep delivery history
beyond the 7/30-day retention, (b) a second consumer of diagnostics (a dashboard or alerting) appears, or (c) operators cannot collect
container logs. Acceptance tests for that issue: a failing/hanging sink does not delay publication or another subscriber; records are
redacted and ≤ cap; two enabled sinks both receive each record; disabling one leaves the other untouched.

## Consequence for the event bus

Publication and delivery never depend on any logging or diagnostic component: the hub logs through the standard `logging` module (a synchronous
handler, as everywhere in the app; a logging failure does not raise into publication) and records outcomes in memory and in the delivery rows it
already writes. A future remote sink must be asynchronous and contained for the reasons in Decision 3. No change to `docs/events.md` decisions is needed.
