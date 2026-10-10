# Companion apps: integrating with Themis (BIZ-172)

For Ordinus, Shop Connector and any future application that creates work in Themis or follows it. **Use only the public HTTP API and
webhooks described here. Never read or write Themis's SQLite database or its data directory** — the schema is private and changes
between releases (migrations), and writes bypass the transactions, events and webhooks that keep Themis consistent.

## 1. API key and scopes

Create a key per application (Settings → API keys) with the narrowest scopes it needs; a key never gets more than you grant.

| Task | Endpoint | Scope |
|---|---|---|
| Create / look up / update a project | `POST/GET/PATCH /api/v1/projects`, `GET /projects?source_app=&external_ref=` | `projects:write` / `projects:read` |
| Add STLs and items, then generate jobs | `POST /api/v1/files/upload`, `POST /projects/{id}/items`, `POST /projects/{id}/promote`, `POST /projects/{id}/generate` | `files:write`, `projects:write` |
| Follow production | `GET /projects/{id}`, `GET /projects/{id}/jobs`, `GET /jobs/{id}` | `projects:read`, `jobs:read` |
| Manage webhook destinations | `/api/v1/webhooks` | `settings:read` / `settings:write` (admin-level: give it to the person configuring the integration, not to the app's runtime key) |

An integration that only creates projects and reads progress needs `projects:write`, `projects:read`, `files:write`, `jobs:read`.
Webhook delivery needs no key on your side: you receive signed POSTs (below).

## 2. Retry-safe project creation: `source_app` + `external_ref`

Send your own stable id for the thing you are creating:

```json
POST /api/v1/projects
{ "name": "Order 1001", "source_app": "shopconnector", "external_ref": "order-1001" }
```

`(source_app, external_ref)` is **unique**. The first call creates the project (`201`); any later call with the same pair — a retry after
a timeout, a duplicate webhook on your side, two workers racing — creates nothing and returns the existing project **unchanged** (`200`).
`external_ref` requires `source_app`; refs are scoped per source app, so two apps may use the same value. Look a project up later with
`GET /api/v1/projects?source_app=shopconnector&external_ref=order-1001`. (`source_layout_id` is Ordinus-specific and unrelated.)

## 3. Retry-safe requests: `Idempotency-Key`

For calls that have no natural key — notably **`POST /projects/{id}/generate`**, which creates files and jobs — send
`Idempotency-Key: <opaque string, ≤200 chars>` (a UUID per logical operation). Behaviour, per endpoint:

* first request: runs normally and its response is stored;
* same key, same request again: **the stored response** is returned with `Idempotent-Replay: true`; nothing else is created;
* same key while the first is still running: `409`; retry after a moment;
* same key with a *different* body: `422`;
* the first request failed (any non-2xx): the key is released and the retry runs for real;
* stored responses are kept 24 hours; a claim abandoned by a crash is taken over after 15 minutes.

It is also accepted on `POST /api/v1/projects`. Keys are scoped to the endpoint (and project), so reusing a string elsewhere is harmless.
Without the header, `generate` is not idempotent: calling it twice generates twice.

## 3b. Webhook destinations

`POST /api/v1/webhooks` `{ "name", "url", "secret", "events": [], "enabled": true }` — as many as you need, each with its **own** secret,
event filter (empty = every event) and enabled flag. `GET` lists them with `last_attempt_at / last_success_at / last_status / last_error`
(the outcome of the latest delivery after retries); `POST /webhooks/{id}/test` sends one signed `webhook.test`. The secret is write-only.
The older `GET/PUT /api/v1/settings/webhook` is a view of the destination named `default`.

## 4. Webhook delivery contract (schema v1)

A JSON `POST` to your URL. **Body**:

```json
{ "event": "project.created", "event_id": "5f0c…", "schema_version": 1, "timestamp": "2026-10-10T09:00:00+00:00",
  "project_id": 12, "name": "Order 1001", "stage": "queued", "source_app": "shopconnector", "external_ref": "order-1001",
  "occurred_at": "2026-10-10T09:00:00.123456Z" }
```

**Headers**: `X-Webhook-Id` (= `event_id`), `X-Webhook-Event`, `X-Webhook-Timestamp`, and — when the destination has a secret —
`X-Webhook-Signature: sha256=<hex HMAC-SHA256 of the raw request body keyed with the secret>`. Verify against the **raw bytes**, with a
constant-time comparison; reject a missing or wrong signature.

**Events** (new fields are only ever added; check `schema_version`):

| Event | When | Extra fields |
|---|---|---|
| `project.created` | a project is created (not for an idempotent repeat) | `project_id, name, stage, source_app, external_ref` |
| `project.stage_changed` | `POST /projects/{id}/promote` | same + `previous_stage` |
| `project.generated` | `POST /projects/{id}/generate` created jobs | same + `job_ids` |
| `job.complete`, `job.failed`, `job.blocked` | job state changes | `job_id`, and for a job in a project `project_id, source_app, external_ref` |
| `printer.alarm`, `spool.low`, `inventory.*` | printer / inventory notices | event-specific (see `docs/agent/backend.md`) |

**Deduplicate on `event_id`** (also sent as `X-Webhook-Id`). The id is identical for every destination and every retry of one event, and
`job.complete` keeps its id even if Themis redelivers it internally, so a receiver that has seen the id should answer `2xx` and do nothing.

**Retries (bounded)**: at most **3 attempts** — immediately, then after 2 s, then after 10 s — and only after a network error/timeout,
`429` or `5xx`. Any other response, including other `4xx`, is final. Answer within 5 seconds. After the last attempt Themis gives up and
records the failure on the destination; deliveries still waiting to retry are lost if Themis restarts, so reconcile with
`GET /projects?source_app=…` if you must not miss one. Delivery order across events is not guaranteed.

## 5. What Themis guarantees, and what it does not

* Project creation and generation are exactly-once **per external ref / idempotency key**.
* Webhooks are at-least-once while Themis is up; use `event_id` to drop duplicates.
* Themis does not push to you across restarts or long outages: your app should poll or reconcile periodically.
* The internal event bus behind these webhooks is described in `docs/events.md`; companion apps do not use it directly.
