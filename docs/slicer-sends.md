# Orca slicer send contract (BIZ-151, BIZ-375–378)

Deploy migrations 69–71 before using the completed plugin. Existing jobs/items retain null requirements/preferences
and existing behavior. These additions are independent of the earlier group-overrides migration 68.

## Footprints and target settings

Jobs and project items accept/return `min_bed_mm: {x, y, z?}` in millimetres. X/Y must be finite and positive;
height must be finite and nonnegative. A printer fits X/Y in either orientation. Its own build height wins,
then the cached machine preset's height; unknown height does not exclude it. Model-target materialization skips
small printers and drops stale generated configs after bed changes. Explicit targets are checked at claim;
already-sliced work is checked before upload as well. The unmatched sweep explains the required bed size.
Locked plate project items carry the requirement into fixed jobs; repacked objects use the constructor's fit checks.

`printer_configs[].overrides` and `model_targets[].overrides` are validated against the active format provider's
**curated** settings contract. Unknown keys, containers and non-finite values are rejected. Booleans become `1`/`0`.
This retains Themis's existing provider allowlist rather than allowing arbitrary machine/post-process keys from
an external slicer. The plugin flags unsupported preset differences for an explicit Ignore/manual decision.
Slice merge order is printer defaults, job/group overrides, then target overrides. Resolved overrides are included
in the production request and therefore the cache key. Pre-sliced target overrides are rejected, not applied.

Project group targets can also store overrides, `filament_profile` and per-model `filament_map` entries. Fixed
generation and pool claims snapshot those settings into jobs/configs. A map entry may carry a catalog
`filament_profile`; production uses it for that model filament's resolved physical tool. Conflicting presets for
two model filaments on one physical tool are rejected instead of silently choosing one. Presets affect cache keys.

`printer_preference: {entries: [printer_id | model_uuid], strict: false}` is accepted by job creation and group PATCH.
The entries are ordered and distinct. Preference only ranks existing eligible targets. A ready preferred printer
gets first chance; a busy, quiet-hours, filament-mismatched, undersized or otherwise ineligible preferred printer
does not delay fallback. With `strict: true`, non-entry printers cannot claim. Pool eligibility also honors strict
preferences, with ready preferred pool printers considered first.

## Atomic sends and updates

`POST /api/v1/slicer-sends` takes:

```json
{"send_id":"<client UUID>","source_app":"orcaslicer","kind":"job","job":{
  "uploaded_file_id":42,"plate_number":1,"model_targets":[{"model_uuid":null}]
}}
```

For a project send, use `kind: project`, exactly one of `project_id` / `project_name`, `group` (GroupPatch), and
`items` (ProjectItemCreate array). Item group IDs are server-owned. A new project starts in planning. This endpoint
does not auto-generate project jobs. The group and every item, or the job and every config, commit together with a
durable send ledger under the SQLite writer lock. Jobs/items retain source-app/send identities. Upload is separate
and already deduplicates content; a rejected send can leave an uploaded library file, but no partial work.

The first request returns 201. Repeating its UUID and normalized payload returns 200 with the same IDs, including
concurrent requests and retries after a lost response. A different payload returns 409 unless `update: true`.
An update preserves a queued/blocked job's ID, replaces file/plate/targets/overrides/footprint/preferences, clears
stale slices/estimates, and requeues it. Slicing/printing/later work and physically started work cannot be updated.
Items and their generated jobs can be updated while all affected jobs remain queued/blocked and contain only that
send's items. The old jobs are cancelled and only the imported group is regenerated in the same transaction.
Started work, active pools and packs shared with unrelated items are protected (409); choose a new send instead.
If repacking fails, the old work and ledger remain unchanged and newly staged artifacts are removed.
Old library files are not deleted by an update, since other jobs may reference them.

`GET /api/v1/slicer-sends/{UUID}` returns the created IDs, current job status and `can_update`. An unknown UUID is 404.
Both operations authenticate before body/path validation. They require `jobs:read`; POST also requires `jobs:write`
or `projects:write` for its destination; project lookup additionally requires `projects:read`.

`GET /api/v1/auth/me?include_identity=true` adds the API key's `key_name`. A staff key has a label, not a human-owner
relation, so no owner is invented. The secret, hash and other credentials are never returned. Omitting the query
preserves the earlier response shape.

Regression tests cover oversized materialization/claims, fleet changes, rotation/height, target isolation and
preference claiming, atomic rollback, concurrent replay, safe updates, source identity, migrations and cache inputs.
