# Artifact storage interface (BIZ-256)

Epic BIZ-265. Builds on `laminus-contract.md` (BIZ-248: `Artifact.path` is a local path), `3mf-ownership.md` (BIZ-259) and
`3mf-thumbnails.md` (BIZ-258). Audit of 2026-10-12 at `develop`. Code: `backend/app/services/artifact_store.py`; proof:
`tests/services/test_artifact_store_contract.py` (one suite, local + in-memory fake), `test_local_artifact_store.py`.

**Additive only: no existing caller uses the interface.** No remote backend (NAS/S3/other) is implemented, chosen, or implied;
that needs its own decision after the gates in section 6. Local disk stays the only runtime behavior, byte- and name-compatible.

## 1. Consumer inventory (everything that touches stored bytes under `/data`)

Roots: `library` (`get_library_dir()`, default `<data>/library`, override `THEMIS_LIBRARY_DIR`), `filecache` (`<data>/filecache`),
`data` (`THEMIS_DATA_DIR`: `gcode/`, `gcode_estimates/`, `gcode_verify/`, `gcode_manual_complete/`, legacy `uploads/`).
DB rows hold only `uploaded_files.relative_path` (library-relative, never absolute; `stored_path` is legacy, cleared by v016/migration)
and `plates[].thumbnail_path` (an absolute filecache path string, see C2).

| # | Consumer (file) | Reads | Writes / deletes | Needs real path? | Class |
|---|---|---|---|---|---|
| **Library: uploaded models, packs, sliced versions** | | | | | |
| L1 | Upload `files.upload_file` (~250-330) | stream in | streams to `.part` in target folder, hashes, `shutil.move` to final; dedup by hash+folder; `unique_path` naming; unlink on rejected eligibility | writes: no (stream); then calls `_parse_plates(dest)` | **Migratable** (`writer`+exclusive create). Follow-up BIZ-285 |
| L2 | Download `files.download_file` (~770), thumbnails GET (~755) | `FileResponse(path)` | - | serves a path (Starlette can stream a handle) | Migratable (`open`). BIZ-285 |
| L3 | Rename/move `files` (~436-500), `_move_versions_with` | - | `Path.replace` with rollback of already-moved versions | no | Migratable (`move`); rollback logic stays in caller. BIZ-285 |
| L4 | Delete `files` (~550-575) | - | unlink after DB commit; `rmtree(filecache/<id>)` | no | Migratable (`delete`/`delete_prefix`). BIZ-285 |
| L5 | `LibraryScanner.scan` / `unique_path` / `rglob` | full tree walk, `stat`, `sha256_file`, `_parse_plates` | - | walk: no (`list`); parse: **yes** (zipfile/PNG extraction by path) | List migratable; parse is path-bound (C1). BIZ-286 |
| L6 | `library_scanner.fresh_content_hash` / `refresh_content_hash`; used by queue_engine (851, 900, 960, 1172), projects (1167, 1318) | `stat` size+mtime, rehash if changed | - | no | Migratable (`stat` + `sha256`). **Constraint:** change detection relies on mtime+size; a remote store must supply an etag or content hash through `ArtifactInfo` before this is portable. BIZ-286 |
| L7 | `migrate_legacy_uploads` (library_scanner ~263; `main.py` lifespan) | legacy `/data/uploads/*` | `src.replace` into `Job Uploads/` | yes (one-shot, local-disk-to-local-disk) | **Intentionally local** (legacy migration; sentinel file `.legacy_migrated`) |
| L8 | Pack output `projects.py` ~1294-1460 | STL paths -> `slicing.pack_models(paths)` (Laminus gets paths/bytes); `Path.write_bytes(packed)` into `Job Pack 3MFs/`; move into per-label subdir; unlink old pack | yes (packer input is a path list) | Input path-bound (`materialize`); output migratable (`put`/`put_file`, `move`). BIZ-287 |
| L9 | Slice save `slice_saver` (~91-200) | artifact path (local slicer output) | `_copy_exclusive` (xb, `unique_path` retry) into the source model's folder; hash; `_parse_plates`; `_cleanup` on duplicate | copy source is a local path (C3) | Destination migratable (`put_file`, exclusive). BIZ-287 |
| L10 | Cached-slice reuse `queue_engine` ~1172-1210 | `library_abs_path(cached.relative_path)` -> `_stage_gcode` | copy into `gcode/<job>/` | yes (stage = materialize) | Migratable via `materialize`. BIZ-287 |
| L11 | Pre-sliced gcode jobs `queue_engine` ~949-1000, `_stage_gcode` (1245) | library file | `shutil.copyfile` to `<data>/gcode/<job_id>/<safe>_j<id><ext>` (keeps full extension; printer type is chosen by it) | yes: staging dir is the job's local working copy | Source via `materialize`; staging dir **intentionally local** (C3). BIZ-287 |
| L12 | Slicing input: `queue_engine` 194/354/950/1120, `jobs.py` 1070 (`source_3mf=<path>`), estimate slice (389) | library 3MF path | slicer output into `gcode/`, `gcode_estimates/` | **yes**: `SliceRequest.source_3mf` is a path handed to the provider | **Blocked by constraint** C3 (BIZ-248: `Artifact.path` local; sidecar contract is path-based) until BIZ-260 settles. Use `materialize` when it does |
| L13 | `jobs.py` ~135 (`_apply_gcode_estimate`), ~525 (`source_has_project_settings`), `files.py` ~727/744 (model filaments, embedded settings) | `parse_estimates`, zip reads by path | - | **yes**: `FormatProvider`/`zipfile` take a path | Path-bound (C1); `materialize` wrapper per call. BIZ-286 |
| **Filecache: thumbnails** | | | | | |
| C2 | `_parse_plates` writes `filecache/<id>/thumbnails/*.png`; `files.get_thumbnail`; `slice_saver` thumb_dir; `regen_file_thumbnails`; delete `rmtree(filecache/<id>)`; `scripts/reextract_thumbnails.py` | PNG files | PNG files | writes by the parser into a dir; the DB stores an **absolute** path string (`thumbnail_path`) of which only the basename is used for serving | Serving/delete migratable (`open`, `delete_prefix("<id>")`). Writer is the parser (path-bound, C1). Regen code is deleted by BIZ-284, do not migrate it. **Constraint:** stored absolute `thumbnail_path` must become a key (migration) before any non-local filecache. BIZ-286 |
| **Data dir: working files** | | | | | |
| C3 | `slicer_service.slice` (`<data>/gcode/<job_id>/`), `_inject_thumbnail` (rewrites gcode in place), estimate/verify/manual-complete dirs (`gcode_estimates`, `gcode_verify`, `gcode_manual_complete`; `rmtree` after use), `queue_engine` cleanup `os.remove(gcode_path)`, `slicer_service` `copy2(source, prepared)` | slicer output | slicer output | **yes**: Laminus/Orca write to a local `output_dir` and `Artifact.path` is local (BIZ-248 section 2/D1 note) | **Intentionally local**: scratch workspace, not a storage tier. Outputs worth keeping are *published* (L9) |
| P1 | Printer upload `queue_engine._do_upload_and_print` (~1385) | `open(gcode_path,'rb').read()` -> `client.upload_file(data: bytes, name)` | - | no, but loads the whole file into memory (pre-existing) | Reads from a local staged path today; if staging moves, use `materialize`/`open`. The bytes-based `AbstractPrinterClient.upload_file` signature is a plugin API, **not changed here** (documented limit). BIZ-287 |
| P2 | Printer "copy file to library" `printers.py` ~1203-1230 | bytes from printer | via L1 (`upload_file` with `BytesIO`) | no | Follows L1 |
| P3 | Start print from printer-stored file `printers.py` ~1037 | printer-side id | - | printer storage, not Themis | **Out of scope** (not Themis artifacts) |
| **Not artifacts** | | | | | |
| N1 | SQLite DB (`database.py`), `plugins_dir` + installer, `hms_messages.json` (`plugins/bambu/alarms.py`), `.legacy_migrated` sentinel | | | yes | **Intentionally local**: DB/code/plugin state, not user artifacts |
| N2 | Camera streaming | | | no files under `/data` today | n/a (no camera artifact storage exists; if recordings are added they are a new artifact class) |

Totals: 14 consumer groups touch stored bytes. Migrated today: **0** (additive). Migratable by `ArtifactStore` ops: L1-L4, L6, L9 destination,
L10, L11 source, L8 output, P1/P2 reads, C2 serving/delete. Intentionally local: L7, C3 scratch dirs, N1. Blocked by a documented constraint:
L12 (provider takes a path), L5/L13/C2-writer (parsers take a path), L6 (needs etag/hash for remote), C2 stored absolute paths.

Path-only consumers that need `materialize`: slicer (`source_3mf`), `three_mf_parser` (`zipfile`), `FormatProvider.parse_estimates`,
`source_has_project_settings`, `extract_gcode_thumbnail`, packer input, anything shelling to a binary (Orca). None of them can read a stream today.

## 2. Interface (`ArtifactStore`)

One store instance per **root** (library, filecache); keys are `/`-separated relative paths with no `.`/`..`/empty segments, no backslash or NUL
(`validate_key`). **Library key == `uploaded_files.relative_path` exactly** (same bytes, same folder semantics), so existing rows and files are valid
without migration. Flat namespace: directories are not objects (`delete_prefix`, whole-segment `list(prefix)`); folders vanish with their last artifact.

| Op | Contract |
|---|---|
| `writer(key, overwrite=False, expected_sha256=None)` | context manager yielding `ArtifactWriter.write(bytes)`; sha256 + size computed while streaming; commits at exit, aborts (nothing visible, temp removed) if the block raises; `info` set after commit |
| `put(key, stream)`, `put_file(key, path)` | convenience over `writer`, 1 MiB chunks, never whole-body in memory |
| `open(key)` | binary read stream; a later overwrite does not disturb an open reader |
| `stat(key)` / `exists` | `ArtifactInfo(key, size, mtime, sha256=None)`; `sha256(key)` streams a hash (a full read: the caller opts in) |
| `list(prefix="")` | sorted by key, excludes in-flight temps |
| `materialize(key)` | context manager -> **read-only** local `Path`; final name segment preserved (kind detection is by suffix, `.gcode.3mf`) |
| `delete(key, missing_ok)`, `delete_prefix(prefix)` | `delete_prefix` returns count, idempotent |
| `move(src, dst, overwrite=False)` | rename; atomic locally; remote = copy then delete (dst complete before src goes) |

**Atomicity.** A key is absent or complete. Local: write to a hidden `.<name>.<rand>.part` in the destination folder, `fsync`, then `os.replace`
(overwrite) or `os.link` (exclusive create: atomic, race-free, one winner); no-hardlink filesystems fall back to reserving the name with `O_EXCL`
then renaming. Folder is `fsync`ed best-effort. Exclusive create is the default because every current writer avoids clobbering (`unique_path`,
`xb` in `_copy_exclusive`); callers keep their own naming policy (`"name (2)"`) by retrying on `ArtifactExists`. Naming is **not** a store feature.

**Errors.** `ArtifactError` base; `InvalidArtifactKey` (bad key, or a symlink resolving outside the root, matching `files._safe_subpath`),
`ArtifactNotFound`, `ArtifactExists`, `ArtifactIntegrityError` (`expected_sha256` mismatch, nothing committed), `ArtifactStoreError`
(backend I/O: disk full, permission, network). A caller's own exception inside `writer`/`put` (a dropped upload stream) propagates unchanged and
commits nothing; it is not relabeled as a storage failure. Callers map `ArtifactNotFound` to today's 404s and `missing=True` rows; the scanner's
"vanished file" semantics are unchanged.

**Blocking.** Sync API; call via `asyncio.to_thread` (the queue loop must never block on storage; backend-review rule).

**Local-disk reference semantics (preserved).** Filenames and folder layout: identical (key = relative path). Relative-path migration: identical
(`library_abs_path` is `local_path`; `relative_path` remains the only persisted pointer). Permissions: files get the umask-governed mode of a plain
`open()` (`0666 & ~umask`), as `slice_saver` copies and scanned files have. **Difference to resolve at rewire (BIZ-285):** today's upload goes through
`NamedTemporaryFile` and ends up `0600`; the store would make it `0644` under the default umask. Single-user container, so low risk, but BIZ-285 must pick
one explicitly. Durability: stronger than today (file `fsync` before publish); no observable change otherwise.

**`local_path(key)`** exists on `LocalDiskArtifactStore` only, deliberately **not** on the ABC: it is the migration escape hatch for path-bound
callers and lets a remote backend be written without ever having to produce a path. `library_abs_path`, `get_library_dir()`, `get_filecache_dir()` and
`get_data_dir()` are **not removed or deprecated**: they stay until every caller in section 1 is migrated or explicitly exempted (acceptance criterion).

## 3. Path-only consumers: materialization, cleanup, invalidation

- **Pattern:** `with store.materialize(key) as path: parse_three_mf(str(path))`. Local: the stored file itself, zero copy, no cleanup. Remote: private
  temp dir, removed on exit including on error (fake backend proves this); path invalid afterwards; consumers must not write to it.
- **Outputs of path-bound producers** (slicer, packer) are written to a local scratch dir (C3, intentionally local), then **published** with `put_file`.
  Scratch is deleted by its creator as today; publish-then-delete order keeps today's "copy first, the job artifact is short-lived" rule (`slice_saver`).
- **Cache invalidation:** v1 has no persistent materialization cache; every `materialize` is a fresh copy on remote backends, so there is nothing to
  invalidate. If a remote backend later adds one, its key must be `(key, size, mtime-or-etag, sha256)` and it must be dropped on `put(overwrite)`/`delete`/`move`
  through the store. The slicing cache is a different layer: it keys on **content hash** (`slice_cache.cache_key`, BIZ-248 section 7), not on location, so
  moving bytes between backends never invalidates cached slices and a stale hash is caught by `fresh_content_hash`.

## 4. Relationship to the Laminus contract

`contract.Artifact(path, kind, produced_by)` is a *transient local output* of a provider call; it stays a local `Path` (BIZ-248). The store is the
*durable* home: slicer/packer output -> `put_file` -> a store key. Provider input `source` stays a path obtained via `materialize`. Nothing in
`contract.py`, `config.py` or the Laminus plugin is touched (BIZ-260 owns those).

## 5. Follow-ups (each narrow, parent BIZ-265; none is a remote backend)

- **BIZ-285 Library CRUD routes onto the store**: L1-L4 (upload, download, thumbnails GET, rename/move, delete) + the upload `0600` vs `0644` decision.
- **BIZ-286 Library scan, hashing, parsers and thumbnail paths**: L5, L6, L13, C2 (materialize for parsers; `thumbnail_path` absolute -> key migration; etag/hash on `ArtifactInfo`).
- **BIZ-287 Slice/pack/stage pipeline**: L8-L11, P1 (publish outputs with `put_file`; stage via `materialize`; stream gcode to printer). Sequence after BIZ-260 settles provider paths; L12 stays path-bound until then.

## 6. Gates before any remote backend is approved (not decided here)

A NAS/S3 backend needs, written and approved first: (1) BIZ-285 to BIZ-287 done, or each remaining caller exempted in this note; (2) an explicit decision record
for the backend (consistency model, auth, cost, whether `move` may be non-atomic, how `mtime` change-detection is replaced by etag/hash);
(3) **switching/migration plan**: copy-verify (`sha256` equal) every library/filecache key to the new store, flip a single store-selection setting,
keep the local copy until verified; (4) **rollback**: because keys equal `relative_path` and nothing is deleted from local disk until a verified
cut-over window passes, rolling back is flipping the setting back with no DB change (a DB-resident store id would break this, so none is added);
(5) a conformance run of `test_artifact_store_contract.py` against the new backend (add it to the `store` fixture); (6) a decision on the
bytes-based `AbstractPrinterClient.upload_file` and on Laminus input paths. Until then local disk is the only behavior and this interface is inert.
