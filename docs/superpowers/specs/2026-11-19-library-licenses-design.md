# Shared licenses and project badges (BIZ-348, design 2 of 2)

Status: implementation proposal. Builds on [the library/source design](2026-11-19-library-aggregator-manyfold-design.md).
This design records product behavior; it does not interpret a license's legal terms or perform royalty reconciliation.

## Outcome

A library file points to a reusable license record. A creator's subscription can cover many files and can be changed once.
Every project's Parts card shows counts such as “1 non-commercial · 2 royalty · 1 unknown”, with a License column and
per-row details. Badges warn; they never block promotion, slicing, queueing or printing. This applies to every project,
including projects without customers. BIZ-347 adds monthly reporting later.

## Records and resolution

Proposed core `licenses` record: stable UUID, display name, holder/artist name, controller URL, optional SPDX code,
commercial use (`none`, `limited`, `full`), attribution-required and share-alike flags, royalty kind (`none`, `per_unit`,
`percent_of_sale`, `flat_per_period`), royalty value, optional period unit cap, subscription start/end and
sell-after-subscription flag. Store audit timestamps and archive state. Creator-to-license bindings are keyed by
`(source_plugin_id, source_creator_id)` with a unique constraint. Source creator IDs are opaque strings, not display names.

One configured application currency is used. Money is stored as integer minor units with currency code; percentage as
an exact decimal string, never a floating-point rate. Require nonnegative amounts/caps, percentage in [0,100],
start <= end, and royalty-kind-specific values. A period definition is required for per-period terms/caps; a bare number
with no period is incomplete and must produce Unknown/review status. Do not calculate or pay royalties in BIZ-348.

Seed immutable built-in CC0, CC BY, BY-SA, BY-NC, BY-NC-SA, BY-ND and BY-NC-ND records with explicit supported SPDX
versions. Keep license URLs/version identity; do not collapse different CC versions into one code. Include Unknown as
a first-class resolution result. Unsupported SPDX/custom terms remain Unknown; preserve the raw code/URL for review.
User-created subscription records are separate from built-in definitions.

Resolution precedence:

1. An explicit file-level assignment made in Themis.
2. A creator binding for `(source plugin, creator ID)` (one subscription per Manyfold creator, as agreed in BIZ-348).
3. A supported exact SPDX code from the source.
4. Unknown.

An explicit assignment is an override, not a sync-managed field. Source synchronization refreshes source facts without
clobbering it. Adding/changing a creator binding immediately changes the effective license of existing entries without
requiring reimport or rewriting each file. Deleting a binding returns to the next resolution tier. Do not auto-create
subscription records for discovered creators. Preserve creator and license attribution when source plugins are disabled.

For built-in uploaded files, store a nullable explicit license assignment and optional user-entered source_url; no source
plugin is required. For remote entries, resolve from catalog facts and bindings, copying origin references onto
materialized file provenance so queued/local work keeps its attribution. Keep raw provenance separate from editable
Themis assignments. Source URL normalization remains a source-adapter concern as described in design 1.

## Current warning and historical facts

The project view resolves current effective terms in one backend batch, avoiding per-row remote calls and eliminating
browser-only license interpretation. Evaluate subscription dates in the configured application timezone and expose an
explicit evaluated-at timestamp. Before start or after end is inactive. At the end timestamp the record is no longer
active; date-only end values must be converted to the next local midnight (exclusive) so the selected day is included.

When a job is completed, a future royalty/audit event should retain the effective license UUID plus a versioned snapshot
of the terms used. Changing a subscription later updates current warnings but must not rewrite historical obligations.
Implement the snapshot contract before BIZ-347 computes reports; BIZ-348 does not invent a reconciliation ledger.

Warning priority (highest wins per file/part):

- Red: commercial use is none; or a subscription is inactive and selling after lapse is not allowed.
- Yellow: royalties apply; terms are unknown/incomplete; subscription expires soon; or a limited-commercial cap needs
  review. Attribution/share-alike obligations are visible in details even when there is no commercial restriction.
- Grey/none: known fully open commercial terms with no applicable warning.

Reversible defaults: “expiring soon” is 30 calendar days, configurable later. An expired license with sell-after-lapse
allowed is not automatically red, but its period/royalty terms remain visible. Future-start subscriptions are treated as
inactive, rather than being interpreted as already granting permission. Limited-commercial records without an enforceable
cap description show review/unknown; BIZ-348 does not gate production based on a cap.

Counts in the Parts header count affected rows, not requested print quantity or inferred royalty units. Return independent
category counts so a royalty-bearing unknown row can be reflected in both labels; each row uses its highest severity.
Do not imply those counts are mutually exclusive. BIZ-347 will define billable units separately.

## API and user interface

Use neutral core endpoints for license CRUD, creator bindings and per-file assignments, with read/write scopes following
existing settings/file conventions and explicit request models. Produce a versioned `license_summary` DTO for library file
and project-item details: effective license identity, source tier, severity, reason codes, evaluated timestamp, creator,
controller/source URLs, obligations and royalty terms. Bulk project summaries are computed server-side from the same
resolver so badge colors, counts and popovers cannot disagree.

Settings → Licenses lists built-in and user records, archived records on request, and creator bindings. Secret values do
not belong here. File detail offers an explicit assignment, “use creator/source default”, controller license link and
Open in source. Project Parts implements agreed style B: header counts, a License column with a labeled chip, then a
popover containing reason text and the two links. The chip must work by keyboard and does not communicate severity by
color alone. Show Unknown clearly, including for local uploads with no assignment.

Avoid hard deletes of referenced records; archive them and preserve references/history. Removing an unused user record
can be a separate explicit operation after reference checks. Built-ins cannot be edited/deleted through ordinary CRUD.
Source metadata and warning changes never silently modify current job configuration or affect queue eligibility.

## Manyfold setup documentation

The source guide must explain: one creator per subscription, the creator's link to its Patreon/MakerWorld/controller,
and exact SPDX licenses on freely licensed models. A Themis creator binding is necessary for membership terms; a source
creator name alone conveys no subscription. Importing license metadata trusts the configured source's attribution but
does not verify the operator's rights or payment status.

## Delivery and acceptance

1. Reusable license/binding/assignment schema, seed data and migration/rollback coverage. Preserve all existing file/job
   rows and source facts. Keep built-in data migrations idempotent.
2. Pure effective-license resolver with tests for precedence, live creator binding changes, source disable, exact SPDX,
   unknown terms, start/end boundaries/timezone, sell-after-lapse, expiring-soon and severity/count combinations.
3. CRUD and batch summaries with authorization, validation, archived-reference and historical-snapshot contract tests.
4. Settings/file assignment and style-B project UI. Browser checks prove badge counts/details update when a creator
   binding changes, Unknown persists, and non-commercial warnings never block ordinary project actions.
5. Backend/browser integration against real DB plus provider-boundary checks. No network calls are needed for badge
   computation. Reconciliation, actual royalty billing/payment, currency conversion, customer rights attestations and
   automatic customer-facing attribution remain outside BIZ-348.

## Decisions requiring implementation evidence

The storage/app timezone and currency settings must be checked before selecting final wire names/migration columns.
If the app has no established single currency/timezone, add explicit settings with migration defaults documented rather
than infer them from the browser. Runtime Manyfold SPDX/creator inheritance must be verified with versioned fixtures;
source file fields win when supplied, otherwise use documented model inheritance. These checks do not require changing
or writing to a live Manyfold server.
