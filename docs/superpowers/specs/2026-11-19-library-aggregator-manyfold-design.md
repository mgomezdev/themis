# Library aggregator and Manyfold source (BIZ-348, design 1 of 2)

Status: implementation proposal. No database, deployment or remote-library changes are made by this document.
Companion: [licenses and project badges](2026-11-19-library-licenses-design.md).

## Outcome and existing boundaries

The built-in file library remains available without plugins. Enabled source plugins contribute their entire catalogs;
metadata is synchronized first and bytes are downloaded when inspected or used. Jobs and unfinished projects retain
local artifacts, so printing does not require the remote source. Manyfold is the first implementation, not a core branch.

Today `UploadedFile` is a local filesystem index (`backend/app/models.py`); `LibraryScanner` treats the filesystem as
its source of truth. Jobs and project items have foreign keys to uploaded files. Do not create remote-only uploaded-file
rows with empty paths and pretend the scanner understands them. Keep catalog identity separate from materialized files.
All byte publication, hashing and local-path access use the existing ArtifactStore/format-provider boundaries
(`backend/app/services/library_store.py`, `docs/architecture/artifact-storage.md`).

## Provider contract

Add core-defined version-1 `library.source` in routed mode, following the existing capability-definition/host machinery.
Every enabled source can serve concurrently. Each catalog entry binds to one provider plugin ID; resolution never silently
falls back to another plugin. Shared core and UI never import or name Manyfold. A source contributes:

- `list_catalog(cursor, changed_since)` -> entries, next cursor and whether the result is a complete authoritative sweep.
  `changed_since` is a hint; a provider can declare full-scan-only. The cursor is opaque to core.
- `describe(source_file_id)` -> source identity, canonical page URL, downloadable revision/validator, format, filename,
  size, preview reference, collections, creator identity/name/links and source license code/URL.
- `download(source_file_id, revision)` -> bounded stream plus validators; no untrusted URL fetches in core.
- Optional `backlink` and later `browse/search` features. Ordinary sync/test connection do not write to the source.

Use typed DTOs, finite timeouts and explicit unsupported/unavailable/error results. Cancellation and partial pages do not
commit a sweep watermark. Plugin contract/conformance tests cover an absent provider, two simultaneous sources, missing
optional features and a disabled provider with previously cached work.

## Catalog and materialized identity

Proposed `library_catalog_entries`: UUID, provider_plugin_id, source_file_id, canonical source_url, display metadata,
creator/source-license metadata, source_revision/validators, last_seen_sweep, missing, cached_file_id and timestamps.
Use database unique constraints on `(provider_plugin_id, source_file_id)` and non-null canonical source_url. On a race,
rollback only the conflicting insert and return the existing entry. Preserve source identities across resyncs; metadata
updates do not create duplicate files. A provider setting change that points at another server requires an explicit
new-source/reset operation, rather than silently reusing IDs from the old server.

Catalog API DTOs use opaque keys (`file:<id>` for built-in rows, `catalog:<uuid>` for source entries), a display source label
from the host, availability and cache state. Clients do not parse provider IDs. Existing `/files` contracts and integer
UploadedFile foreign keys remain intact. `POST /library/entries/{key}/materialize` returns the local uploaded-file ID,
content hash and revision; job/project creation materializes before linking it. New aggregator list/detail APIs join the
local library with catalog records without requiring remote I/O to list previously synchronized metadata.

Canonicalization is provider-owned: resolve accepted file-page and raw-download forms to the stable file-page URL;
strip query/fragment/trailing slash and normalize scheme/host. Preserve the configured HTTP/HTTPS scheme: the recorded
local Manyfold deployment uses HTTP and a forced HTTPS link would not work without a TLS endpoint. Reject URL userinfo;
preserve case-sensitive paths and opaque IDs. Source hostname/scheme changes remain an identity break; do not silently
merge across origins. The adapter never forwards credentials to a foreign content URL or follows redirects.


## Sync and cache lifecycle

One sweep per provider at a time, both for scheduled sync and Sync now. Page upserts are transactional. Only after a full
successful authoritative sweep mark previously seen-but-now-absent entries missing. An auth failure, timeout, canceled
sync or failed page preserves the prior catalog and never marks everything deleted. Keep last success/error and progress
visible. Source removal never deletes work or its cached bytes.

Download to an ArtifactStore temporary key, stream/hash/limit size, inspect with the format provider, then publish and
link the new uploaded-file row in a short transaction. Concurrent materialization shares a per-entry lock; database
constraints and revision comparison remain the final arbiter across processes. Failed/canceled downloads leave no ready
cache pointer; cleanup can remove abandoned temporary artifacts. Never pass source-controlled filenames as filesystem
paths. Opaque IDs and validators, not filename guesses, identify source revisions.

An unfinished job or project pins every referenced materialized revision. Retain old bytes while replacing the entry's
current cached revision. A source change marks current cache stale and refreshes before the next new slice when the
source is available; a running slice/print keeps the artifact it already acquired. Offline work uses its pinned last-known
copy and shows its age/staleness rather than requiring a live source. This preserves BIZ-348's offline-work guarantee.
New or unmaterialized work cannot proceed without successful download. Tests must make this choice explicit for queued
work and project regeneration; no active artifact is overwritten in place.

Unpinned, source-owned cached copies are evictable under a configurable size limit with least-recently-used ordering.
Never evict user uploads, referenced bytes, active materialization leases or source bytes still needed by a slice-cache
record. Compute pins from durable references and recheck in the eviction transaction. A source disable/uninstall retains
catalog attribution and referenced local files. It stops synchronization and new uncached reads; it does not erase work.

## Manyfold adapter: source evidence and behavior

Baseline: [Manyfold v0.153.0 OpenAPI](https://github.com/manyfold3d/manyfold/blob/v0.153.0/app/api/v0/openapi.json),
read 2026-11-19. This is a versioned source reference, not proof of the user's running instance.

- OAuth2 client credentials: `/oauth/token`; scopes `public`, `read`, `write`, `delete`, `upload`. Import requests use
  `public read`; request `write` only for opt-in backlinks. Never request delete/upload for this feature.
- Use `Accept: application/vnd.manyfold.v0+json`. `/models` documents page/order/creator/collection, with no
  `changed_since` parameter. Implement full paginated sweeps initially; never invent incremental deletion semantics.
- Hydra `member` and `view.next` drive pagination. Follow only the configured source origin, detect repeated pages and
  bound page count. Describe models/files to collect `hasPart`, creator, collections and `spdx:license`.
- File detail provides `contentUrl`, `contentSize`, `encodingFormat`, creator, license and `isPartOf`. Fetch the returned
  content URL after validating origin; do not assume the older `/raw/{filename}` shape is the only download path.
  `updateContentUrl` is a signed write URL and must not be used, stored as a public link or logged.
- Model request/response has `links`; model-file request does not. Backlinks go on the parent model, one labeled link per
  imported file. Read-modify-write preserving unrelated links, serialize writes per model, dedupe owned links and retry
  after a fresh read. If the API supplies no concurrency validator, surface conflict/uncertainty rather than overwrite
  an intervening edit. Backlink failure does not roll back an import.

Settings: base URL, OAuth client ID, write-only client secret, Themis public base URL, sync interval, cache limit and
backlinks toggle (default off). Cache access tokens in plugin memory, honor returned expiry, serialize refresh, and retry
once after 401 with a fresh token. Never log credentials, tokens or signed URLs. Do not assume the historical two-hour
expiry is fixed. Test connection obtains a token and performs one read; reports transport/auth/scope/API errors separately.

Setup docs must show `/oauth/applications`, scopes and where to paste credentials. Exact form-vs-Basic token acceptance,
redirect-URI UI requirements, non-admin owner visibility, ID persistence and live links-update semantics require provider
source/fixture verification before coding those assumptions as supported. No credentials or access grants were changed
in this design pass.

## UI and decisions

The existing hard-coded Manyfold placeholder in FilesScreen must become generic source browsing, keeping the built-in
library available. Show source label, missing/offline/stale state, creator, license summary and Open in source. Detail
inspection may fetch bytes for plate stats and must display progress/failure without erasing cached metadata. Add stable
`/files?file=<local-id>` deep links before backlinks; remote catalog entries also need an opaque-entry deep link.

Decisions (reversible in implementation): sync brings the whole accessible catalog; no cherry-pick MVP. Backlinks are
opt-in and written only when a file is used in a project/job, not on metadata sync. No deployment/source write is
performed until the adapter and its explicit controls exist. Unsupported source metadata remains Unknown, not invented.

## Delivery and acceptance

1. Source capability/DTOs and separate catalog schema with real DB race, partial-sweep and migration tests.
2. Materialization/pinning/eviction through ArtifactStore; tests for unavailable sources, changed files, concurrent
   imports, retained project/job revisions, and crash recovery.
3. Manyfold adapter with recorded versioned responses and OAuth/pagination/download/backlink contract tests.
4. Generic browsing/detail/deep links and settings; browser tests against both a source fixture and real backend.
5. Plugin boundary tests forbid provider names in core/shared UI. Existing built-in upload/scan/rename/delete and
   ordinary queue/slice behavior must remain green. This feature does not require a connected production source to test.

Licensing and royalty calculations follow design 2; BIZ-347 reconciliation remains a separate feature.
