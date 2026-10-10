# BIZ-249 — versioned event contracts and reliable pub/sub v1

Epic BIZ-266. Design note and contract: `docs/events.md` (answers every decision the issue requires). This plan is the
implementation record; consumers are migrated by BIZ-269 / BIZ-252 / BIZ-172, not here.

## Scope (done here)
1. `app/eventing/`: `definitions` (EventDef, EventSubscription, name regex), `envelope` (validated, frozen), `registry` (core catalog,
   plugin-defined merge, `validate_publication`), `redaction`, `hub` (best-effort lanes, durable outbox dispatcher).
2. Plugin manifest: `defines_events`, `subscribes` (validated); host: `event_subscriptions`, `broken_subscriptions`, `deliver_event`
   (contained, redacted); event-only plugins are built when enabled.
3. Persistence: models + migration v045 (`event_outbox`, `event_deliveries`).
4. Operator API `/api/v1/events/*`; hub started/stopped in the lifespan; `openapi.json` regenerated.
5. Tests (`tests/eventing/`, `tests/api/test_events_api.py`): malformed envelopes, ownership, manifest validation, redaction, containment of
   failing/hanging subscribers, ordering, bounded lanes/backpressure, duplicate publication, plugin disabled/removed/dormant, fake
   subscriber + fake definer plugins, durable atomicity (commit/rollback), dedup, retry, dead + operator retry, crash-safe attempt counting.

## Explicitly unchanged
Completion, inventory deduction, maintenance accrual, webhooks and notifications still use their existing paths (no core publisher or
subscriber is wired). `services/events.py` (internal printer callbacks) is untouched.

## Follow-ups (other issues)
BIZ-269 publish `job.complete` in the completion transaction and migrate consumers; BIZ-252 notification channels; BIZ-172 project hooks;
BIZ-270 diagnostics decision.
