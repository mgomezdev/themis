# File Library — Design Spec

**Date:** 2026-06-02
**Status:** Approved (pending spec review)

## Goal

Turn the mock-backed Files screen into a real **model file library**: a browsable, on-disk
directory tree of `.3mf`/`.stl` models, enhanced by an optional DB-backed **tagging system**, with a
placeholder **Manyfold** integration tab. Unify the library with job uploads so jobs draw from it.
Separately, **scrub the Themis-owned filament library** (rely on Spoolman + the printer's current
loaded type/color).

## Core principle

The **filesystem is the source of truth** for file/folder organization. The database plays only two
roles: (1) a thin **index/cache** of what's on disk (stable ids for job references, cached plate
metadata, so we don't re-parse on every scan), and (2) a **tag overlay** for search/filter. Tags
*enhance* discovery; they are never required and are decoupled from where a file physically lives.

## Architecture

```
THEMIS_LIBRARY_DIR/ (default <data_dir>/library/)   ← real folders + .3mf/.stl files (the truth)
  Job Uploads/                                       ← default landing folder for new-job uploads
  Customers/Vela/arm.3mf  ...
<data_dir>/filecache/<file_id>/thumbnails/*.png      ← derived artifacts kept OUT of the library tree
SQLite:
  uploaded_files   ← evolves into the library index (one row per on-disk model file)
  tags, file_tags  ← optional tag overlay (shared with Settings → Tags)
```

Backend subsystems involved: a new `LibraryScanner` service (scan/reconcile disk ↔ index), expanded
`files.py` routes, new `tags.py` routes. No change to the queue/printer subsystems beyond New Job
sourcing files from the library.

## Data model

### `uploaded_files` (evolved — table name kept so `jobs.uploaded_file_id` FK is untouched)
Existing: `id, original_filename, stored_path, plates(JSON), uploaded_at`.
Add (via idempotent `_migrate` ALTER TABLE … ADD COLUMN):
- `relative_path: str` — path within the library root, e.g. `Customers/Vela/arm.3mf`. Natural key.
- `folder: str` — parent folder of `relative_path` (e.g. `/Customers/Vela`); stored for fast filter.
- `size_bytes: int`
- `content_hash: str` — sha256 of file bytes; used for move-detection/tag-relink and dedupe awareness.
- `mtime: float` — disk mtime, to skip unchanged files on rescan.
- `missing: bool` (default False) — set True if an indexed file vanished but a job still references it
  (kept, not deleted, so job history survives).
`stored_path` = absolute path = `library_root / relative_path` (kept for compatibility).

### `tags`
`id, name (unique, non-null), color (str), category (str — e.g. Material/Purpose/Stage/"" ), created_at`.

### `file_tags` (join)
`file_id FK→uploaded_files (ON DELETE CASCADE), tag_id FK→tags (ON DELETE CASCADE)`, PK `(file_id, tag_id)`.

## Backend

### `LibraryScanner` service (`app/services/library_scanner.py`)
- `scan()`:
  1. Walk `THEMIS_LIBRARY_DIR` for `.3mf`/`.stl`.
  2. For each file: compute `relative_path`, `size_bytes`, `mtime`. Compute `content_hash` only when the
     row is new or `mtime`/`size` changed (perf).
  3. Upsert the index row keyed by `relative_path`. If no row matches the path but a row with the same
     `content_hash` exists at a different path (moved/renamed externally) → update that row's
     `relative_path`/`folder` (tags follow, since they key on `file_id`). Clear `missing`.
  4. Parse plates + thumbnails into `filecache/<id>/` when new/changed; update `plates`.
  5. After the walk: rows whose file no longer exists → delete if unreferenced by any job; else set
     `missing=True`.
- Triggers: app startup (mtime-cheap pass) in `main.py` lifespan; manual `POST /files/rescan`;
  targeted upserts after app-driven mutations (upload/move/delete) without a full walk.
- Collision helper: `unique_path(folder, filename)` → suffixes `(2)`, `(3)` on name clash.

### Routes — `files.py` (expanded; prefix `/api/v1/files`)
- `GET ""` — list index rows. Query: `folder`, `tags` (repeatable, AND-match), `search` (filename
  substring), `sort` (`updated|name|size`). Each row serialized with `folder`, `tags` (resolved),
  `size_bytes`, `plates` count, thumbnail URL, `missing`.
- `GET /tree` — folder tree with per-folder counts (server-computed from index rows).
- `POST /upload` (201) — multipart `file` + optional `folder` (default `Job Uploads`). Validates
  extension (`.3mf`/`.stl`, as today), writes into the library at a collision-safe path, indexes it,
  returns the row. Reused by New Job.
- `POST /folders` — body `{ path }`; `mkdir -p` under the root (reject traversal outside root).
- `PATCH /{id}` — body may include `name` and/or `folder`; performs a real disk move/rename to the new
  collision-safe path, updates `relative_path`/`folder`. Tags follow (unchanged `file_id`).
- `DELETE /{id}` — refuse (409) if an **active** job (`queued|slicing|uploading|printing|paused|blocked`)
  references the file; otherwise delete the file from disk + the index row (cascades `file_tags`) +
  the `filecache/<id>/` dir.
- `POST /{id}/tags` — body `{ tag_id }`; add a `file_tags` row (idempotent).
- `DELETE /{id}/tags/{tag_id}` — remove the association.
- `POST /rescan` — run `LibraryScanner.scan()`; return a summary `{ added, moved, removed, missing }`.
- `GET /{id}/plates`, `GET /{id}/thumbnails/{name}` — as today, thumbnails now served from `filecache/`.

### Routes — `tags.py` (new; prefix `/api/v1/tags`)
- `GET ""` — list tags with `usage_count` (count of `file_tags`).
- `POST ""` — `{ name, color, category }`; 409 on duplicate name.
- `PATCH /{id}` — update name/color/category.
- `DELETE /{id}` — delete tag (cascades `file_tags`).
Register both routers in `main.py`.

### Migration of legacy uploads
One-time, idempotent, guarded by a marker (e.g. a `library/.migrated` sentinel or a `queue_config`
flag): move each existing `data/uploads/<uuid>/model.<ext>` into `library/Job Uploads/<original_filename>`
(collision-safe), relocate its thumbnails to `filecache/<id>/`, set `relative_path`/`folder`/`size_bytes`/
`content_hash`/`mtime`. Best-effort: on any per-file error, leave that file indexed in place and log.
Approved by user: move into `Job Uploads/`.

## Frontend

### API clients (`frontend/src/api/`)
- `files.ts` — types `LibraryFile`, `FolderNode`; hooks/fns: `useFiles(filter)`, `getFolderTree`,
  `uploadFile(file, folder?)`, `createFolder(path)`, `updateFile(id, {name?, folder?})`,
  `deleteFile(id)`, `addTag(id, tagId)`, `removeTag(id, tagId)`, `rescan()`, `fileThumbnailUrl(id, name)`.
- `tags.ts` — type `Tag` (`id, name, color, category, usage_count`); `useTags()`, `createTag`,
  `updateTag`, `deleteTag`.

### `FilesScreen.tsx`
- Replace `data/mock` imports with `api/files` + `api/tags`.
- Add tabs **[Library] [Manyfold]** at the top.
- **Library tab**: folder tree (from `GET /tree`), tag-facet filter (groups by tag `category`; counts
  from current result set), file grid, **file detail panel** on card click (thumbnail, file/plate stats,
  assign/remove tags, rename, move folder, delete, **"Use in new job"** → navigates to New Job with the
  file preselected). Toolbar: upload (+ drag-drop onto the grid), **New folder**, sort, rescan.
- **Manyfold tab**: a placeholder panel ("Manyfold integration — coming soon", short blurb). No backend.

### `NewJobScreen.tsx`
- Source selector: **Upload** (existing) **or "Pick from library"** (browse/select an existing
  `LibraryFile`). Selecting a library file skips upload and uses its id.
- On upload: optional **"Save to library location"** folder picker (default `Job Uploads`), passed as
  `folder` to `POST /upload`.
- Entry from FilesScreen "Use in new job" preselects the library file.

### `SettingsScreen.tsx` — Tags tab
- Replace the in-memory `TAGS` mock with `api/tags` real CRUD. "In use" / "orphan" counts derive from
  `usage_count`. Create/edit/delete persist.

### Scrub filament library
- Remove `FilamentsScreen.tsx`, its `<Route>` and `screenConfig` entry in `App.tsx`, its Sidebar nav
  link, and the `FILAMENTS` export + usages in `data/mock.ts`. Remove `getPrinter`/mock usages tied only
  to Filaments if now unused. Spoolman (Settings → Spoolman, the New Job/Fleet Spoolman pickers) is
  unaffected and remains the filament source; printer current type/color stays real via
  `printers.loaded_filaments`.

## Edge cases / error handling
- Extension validation unchanged (`.3mf`/`.stl` only).
- Path traversal: all folder/path inputs resolved and confirmed within `THEMIS_LIBRARY_DIR`; reject
  otherwise (400).
- Name/path collisions: `unique_path` suffixing on upload and move.
- External moves while the app is idle: reconciled on next scan via `content_hash`; tags re-link. A file
  whose content also changed (new hash) at a new path is treated as new (tags don't follow — acceptable,
  tags are non-critical).
- Delete guard: active-job reference → 409 with a clear message naming the job.
- Tag delete cascades `file_tags`; duplicate tag name → 409.

## Testing
**Backend (pytest):**
- Scanner: new file indexed; changed file re-parsed; file moved on disk → same row, path updated, tags
  preserved (hash relink); deleted file → row removed if unreferenced, `missing=True` if job-referenced;
  scan idempotency (second scan no-ops).
- Upload → index row + disk file in target folder; collision suffixing.
- Folder create; rename/move updates path + keeps tags.
- Tag assign/remove; `GET /files?tags=` AND-filter; `GET /files?search=`/`sort=`.
- Delete guard with an active job (409) vs deletable file.
- Tags CRUD incl. duplicate-name 409 and cascade on delete.
- Legacy migration: a fake `uploads/<uuid>/model.3mf` ends up in `library/Job Uploads/` and indexed,
  idempotent on re-run.

**Frontend (Vitest):**
- FilesScreen renders from stubbed `fetch` (folder tree, file grid, tag facets); tag filter narrows
  grid; tab switch shows Manyfold placeholder.
- File detail: assign tag calls the right endpoint; delete calls DELETE.
- NewJob "Pick from library" selects a file and enables create without upload; upload folder defaults to
  Job Uploads.
- Settings → Tags create/edit/delete hit the tags API.
- Removing Filaments doesn't break routing/nav (no dead links; Sidebar renders).

## Out of scope
- Actual Manyfold sync/API (placeholder tab only — a later project).
- 3D/in-browser model preview (thumbnail only).
- Per-part fulfillment tracking.
- Bulk/multi-select file operations (could be a fast-follow).

## File structure (created / modified)
**Backend create:** `app/services/library_scanner.py`, `app/api/routes/tags.py`,
`tests/services/test_library_scanner.py`, `tests/api/test_files_library.py`, `tests/api/test_tags.py`.
**Backend modify:** `app/models.py` (columns + tags/file_tags), `app/database.py` (`_migrate` +
legacy-upload migration), `app/api/routes/files.py` (expanded), `app/config.py`
(`get_library_dir`/`get_filecache_dir`), `app/main.py` (register `tags` router, startup scan).
**Frontend create:** `src/api/files.ts`, `src/api/tags.ts`.
**Frontend modify:** `src/screens/FilesScreen.tsx`, `src/screens/NewJobScreen.tsx`,
`src/screens/SettingsScreen.tsx`, `src/App.tsx` (remove Filaments route/config), `src/components/Sidebar.tsx`
(remove Filaments link), `src/data/mock.ts` (drop FILAMENTS/FILES/TAGS as they're replaced),
`src/data/types.ts` (LibraryFile/Tag types).
**Frontend delete:** `src/screens/FilamentsScreen.tsx`.
**Docs:** update `docs/agent/*` via the `themis-docs-sync` skill after implementation.
