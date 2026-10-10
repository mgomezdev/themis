# Events: contract and delivery (BIZ-249)

Design note for the versioned event contract used by core and plugins. Code: `backend/app/eventing/` (`envelope`, `registry`,
`hub`, `redaction`, `definitions`), routes `app/api/routes/events.py`, tables `event_outbox` / `event_deliveries` (migration v045).
Epic: BIZ-266. Consumers migrate separately (BIZ-269 print completion, BIZ-252 notification channels, BIZ-172 project webhooks);
**in this change nothing in core publishes or subscribes yet**, so completion, inventory deduction, maintenance, webhooks and
notifications behave exactly as before.

The older `services/events.py` bus (typed Python events between printer clients and `PrinterManager`) is unchanged. It carries
*internal callbacks*, not domain events: no envelope, no persistence, in-process only.

## Envelope

Every event, core or plugin, is an `EventEnvelope` (frozen, `extra=forbid`):

| Field | Meaning |
|---|---|
| `id` | unique per publication (uuid hex). Stable across redelivery: **the idempotency key for a handler**. |
| `name` | event class, dotted lower-case: `job.complete`, `acme_ntfy.sent`. |
| `schema_version` | integer ≥ 1; must equal the class's current version at publication. |
| `occurred_at` | UTC ISO-8601, when the transition committed. |
| `source` | `core` or the publishing plugin's id. |
| `entities` | references to what the event is about: `job_id`, `project_id`, `printer_id`, `order_id`, `customer_id`, `file_id`, `spool_ref` (int or str). Other keys are rejected. |
| `dedup_key` | logical identity (`job.complete:42`). Two publications with one key are one event. |
| `correlation_id` | optional, ties events of one request/flow together. |
| `payload` | JSON object ≤ 16 KiB, validated by the class's `payload_model` when it has one. Big data travels by id. |

Publication is validated against the **catalog** (`registry.event_catalog()` = `CORE_EVENTS` + every registered plugin's
`defines_events`): the event must exist, `source` must be its owner, the publisher must *be* that owner (a plugin cannot publish a
core event or another plugin's), the schema version must be current and the payload valid. A violation raises `EventError` to the
**publisher** (a programming error); nothing a subscriber does can ever raise into a publisher.

Core classes (names match the existing webhook/notification event names): `job.complete` (**durable**), `job.failed`,
`job.blocked`, `printer.alarm`, `spool.low` (best-effort).

Plugin events: `PluginManifest.defines_events=(EventDef("acme_x.ready", version=1, durability=…, payload_model=…),)`; names must
start `<plugin id>.`. Subscriptions: `PluginManifest.subscribes=(EventSubscription("job.complete", "on_complete", timeout=, queue_size=),)`
where `on_complete` is an `async def on_complete(self, envelope)` on the plugin instance. Neither is part of `themis-plugin.toml`
(only the manifest), and a plugin that only subscribes or defines events is still built when enabled. A publisher calls
`hub.publish(envelope, as_plugin="acme_x")` / `hub.enqueue_durable(session, envelope, as_plugin="acme_x")`; a disabled plugin
cannot publish.

## Decisions

**1. Which events are durable.** Per class, declared with the class. `durable` = at-least-once, stored in the publisher's
transaction. Only `job.complete` is durable among core events, because its consumers (filament deduction, maintenance accrual)
are state that must not be lost; every other core event is a notice whose loss costs a message, never data. A plugin chooses for
its own events. Durable events must be published with `enqueue_durable`, best-effort ones with `publish`; the wrong call raises,
so a durable event can never be sent by a path that forgets the transaction.

**2. Ordering.** *Best-effort*: each subscriber has its own FIFO lane processed one event at a time, so a subscriber sees events
in the order `publish()` returned for them; there is no ordering across subscribers or across event classes. *Durable*: first
attempts to one subscriber run in outbox order (`event_outbox.id`, assigned inside the publisher's transaction, so it is
commit-order only approximately under concurrent publishers). A retried event can arrive **after** later ones: a failing delivery
never blocks the subscriber's queue (no poison-pill head-of-line blocking). Handlers must not depend on order; they re-read
current state by `entities` and use `occurred_at`/`id` to ignore stale work.

**3. Backpressure.** The publisher never waits. Best-effort lanes are bounded (`queue_size`, default 1000): a full lane drops the
*new* event, counts it (`dropped`) and logs (first, then every 100th). Durable events are never dropped: the backlog lives in the
database (`durable_pending` is exposed; a warning threshold exists); durable classes are low-volume by definition (job completions).
A slow subscriber only fills its own lane / its own pending rows.

**4. Retries and idempotency.** Best-effort: no retry. Durable: delivery is retried with exponential backoff (5 s · 2^(n-1), cap
15 min) up to 8 attempts, then `dead` until an operator retries it (`POST /events/deliveries/{id}/retry`). The attempt counter is
committed **before** the handler runs, so a handler that crashes the process cannot loop forever across restarts. Delivery is
at-least-once: a crash after the side effect but before the acknowledgement redelivers, so **handlers must be idempotent**, keyed
on `envelope.id` (or `dedup_key`). Publisher-side duplicates are collapsed: best-effort by `dedup_key`/`id` (in-memory, 10 000
remembered); durable by a unique `dedup_key` on the outbox (`INSERT … ON CONFLICT DO NOTHING`; the second publication stores
nothing and returns `False` without disturbing the caller's transaction). Making the *publisher* emit one logical event for
duplicate printer callbacks is the publisher's job (BIZ-269 keeps the existing atomic completion claim and uses `job.complete:<id>`).

**5. Atomicity of durable handoff.** `enqueue_durable(session, envelope)` adds the outbox row and one `event_deliveries` row per
current subscriber to the **caller's session**; they commit or roll back with the state change. The caller then calls
`hub.wake()`. Subscribers are fixed at enqueue time: a plugin enabled later does not receive past events (no replay).

**6. Plugin-defined events and optional dependencies.** A subscription may name any valid event name, including one defined by a
plugin that is not installed or is disabled: it is simply dormant (nothing is ever published; no error). Disabling or uninstalling
a *subscriber* stops delivery at once (subscribers are resolved from the plugin host at delivery time, nothing is unregistered).
Best-effort events queued for it are skipped (`skipped_inactive`). A durable delivery already created for it stays `pending`,
costs no attempt, is re-checked every minute and resumes when the plugin is enabled again; after 7 days unavailable it goes `dead`
(finished outbox rows are purged after 7 days). A disabled *definer* cannot publish. A plugin cannot define a core name.

**7. Containment and redaction.** Every handler runs with a timeout (`EventSubscription.timeout`, core default 10 s). A plugin
handler goes through `PluginHost._contained` like any provider call (timeout, exceptions caught, recorded in the plugin's state);
a core handler through `asyncio.wait_for`. Failure text is passed through `redact_error`: the plugin's secret values, URL
credentials and `password=`/`token=`-style pairs are masked, and it is capped at 300 characters. Raw exceptions are never stored.
One failing, hanging or slow subscriber affects only itself: separate lanes (best-effort) and separate delivery tasks (durable).

**8. Operator visibility.** `GET /api/v1/events/catalog`, `GET /api/v1/events/subscribers` (per subscriber since startup:
`delivered/failed/timed_out/dropped/skipped_inactive`, `queue_depth`, redacted `last_error`, `last_ok_at`, and from the outbox
`durable_pending`/`durable_dead`; also lists a plugin handler that is missing or not `async`), `GET /api/v1/events/deliveries?status=dead|pending|delivered`,
`POST /api/v1/events/deliveries/{id}/retry`. Scopes `settings:read` / `settings:write`. A plugin's handler failures also show as the
plugin's `last_error`. Counters are in-memory (reset on restart); the outbox is the durable record. A queryable log/UI, retention
settings, replay and external sinks are **out of scope** (BIZ-270 decides them).

## Delivery guarantees at a glance

| | best_effort | durable |
|---|---|---|
| Stored | no | outbox row in the publisher's transaction |
| Lost on crash | yes | no |
| Duplicates possible | no (deduplicated at publish) | yes (at-least-once; handler must be idempotent) |
| Retries | no | backoff, 8 attempts, then `dead` (operator retry) |
| Order | FIFO per subscriber | first attempts in outbox order; retries may reorder |
| Full / slow subscriber | new events dropped and counted | backlog in the database |
| Publisher blocked by subscribers | never | never |

SSE / WebSocket live updates are **not** part of this contract and make no delivery promise: a reconnecting client re-fetches
state. They may subscribe as best-effort consumers later (BIZ-269).
