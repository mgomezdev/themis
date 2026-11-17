# Orca project import settings (BIZ-151)

The Orca plugin creates an import-specific print group when adding plates or objects to a project. Group targets contain
eligible printers/models and process presets; item filament ask fields contain the import's material constraints. Other
project groups and existing items are unchanged.

`PATCH /api/v1/projects/{project_id}/groups/{group_id}` also accepts `overrides`: a dictionary of process keys from the
active format provider's curated settings. Unknown keys, non-scalar values and non-finite numbers are rejected with 422,
rather than discarded. Values normalize to slicer strings; booleans become `1`/`0`. Omitting the field preserves it;
explicit null or `{}` clears it. Group GET/PATCH responses include `overrides`.

Fixed generation and on-demand pool claims copy the group's overrides into each newly created job. Changing a group
does not rewrite previously created or started jobs. The existing job slicing and cache-resolution paths apply that
job-level snapshot. Pre-sliced G-code items cannot be generated in a group with process overrides.

Migration 68 adds the nullable JSON column, leaving existing groups at null with their previous behavior. Clients can
check the OpenAPI `GroupPatch` schema for the `overrides` property before using it; older servers ignore unknown request
fields, so a plugin must check support before uploading/creating records instead of assuming the field was stored.
