# Orca Sidecar Integration — Implementation Stories

> **Context for the planning LLM**
>
> Themis is a Python/FastAPI print-farm manager that currently calls the OrcaSlicer CLI
> directly via `subprocess` for two operations: (1) slicing a prepared 3MF file into
> gcode, and (2) regenerating plate thumbnails. An Orca sidecar service
> (`projects/orca`, running on port 5000) wraps those same CLI calls behind a REST API
> with async job tracking. The goal is to replace the direct subprocess calls with HTTP
> calls to the sidecar so that Themis no longer needs OrcaSlicer installed locally.
>
> **Key source files in Themis backend:**
> - `app/config.py` — OrcaSlicer executable + config dir resolution, data dir
> - `app/services/slicer_service.py` — main slice orchestration, `_run()`, `_inject_thumbnail()`
> - `app/services/thumbnail_regen.py` — background thumbnail regen loop
> - `app/services/mesh_3mf_builder.py` — injects `project_settings.config` into 3MF
> - `app/services/queue_engine.py` — async queue loop, calls `SlicerService.slice()`
> - `app/main.py` — startup lifespan
>
> **Sidecar API surface (base URL configurable, default `http://localhost:5000`):**
> - `GET  /api/health` — OrcaSlicer install status, active job count
> - `POST /api/slice/prepared` — slice a pre-configured 3MF (embedded project_settings.config); form fields: `file`, `plate` (int), `export_3mf` (optional filename), `geometry_only_retry` (bool)
> - `GET  /api/slice/status/{job_id}` — poll: `{status: pending|slicing|completed|failed, sliced_file, error}`
> - `GET  /api/slice/download/{job_id}` — stream binary output, **evicts job on call**
> - `GET  /api/slice/logs/{job_id}` — SSE stream of OrcaSlicer stdout lines
>
> Stories are ordered by dependency. Stories 1–3 deliver a working slice path.
> Story 4 requires a new endpoint added to the sidecar before Story 5 can be completed.
> Stories are otherwise independent of each other within their dependency tier.

---

## Story 1 — Sidecar Client & Startup Health Gate

### Intent / Need
Themis needs a single, testable way to reach the sidecar and detect early when it is
unavailable. Without this, every downstream story has to invent its own HTTP boilerplate
and error handling. A thin client module and a startup readiness check give every
subsequent story a stable surface to build on, and give operators an immediate signal
when the sidecar is misconfigured or down.

### User Story
As a **Themis operator**, I want the application to refuse to start (or log a clear
warning) when the orca sidecar is unreachable, so that I do not silently queue jobs
that will never slice.

### Acceptance Criteria
1. A new config value `ORCA_SIDECAR_URL` (env var, default `http://localhost:5000`) is
   read by `app/config.py` and exposed via a `get_orca_sidecar_url()` getter. When the
   env var is absent the default is used without error.
2. A new module `app/services/orca_sidecar_client.py` provides at minimum:
   - `async def health() -> dict` — calls `GET /api/health`, returns parsed JSON
   - `async def slice_prepared(file_bytes, plate, export_3mf, geometry_only_retry) -> str` — submits job, returns `job_id`
   - `async def poll_status(job_id) -> dict` — calls `GET /api/slice/status/{job_id}`
   - `async def download(job_id) -> bytes` — calls `GET /api/slice/download/{job_id}`
   All methods raise a single typed exception (`SidecarError`) on non-2xx responses or
   connection failures, with the HTTP status and body included in the message.
3. On application startup (`app/main.py` lifespan), Themis calls `health()`. If the
   call raises or `orcaslicer_installed` is `False` in the response, a `WARNING` log is
   emitted with a human-readable message. Startup does **not** abort — Themis may be
   starting before the sidecar container is ready.
4. A unit test mocks `httpx` (or `aiohttp`) at the transport layer and asserts:
   - `health()` returns the parsed dict on 200
   - `health()` raises `SidecarError` on connection refused
   - `SidecarError` message includes the original URL and failure reason
5. No existing tests are broken. No OrcaSlicer subprocess code is modified by this story.

### Out of Scope
- Circuit-breaker or retry logic (can be added later)
- Sidecar log streaming (Story 3 enhancement)
- Any changes to slicing or thumbnail code

---

## Story 2 — Replace Slice Subprocess with Sidecar Call

### Intent / Need
`slicer_service.py:_run()` currently spawns a local OrcaSlicer subprocess. Replacing it
with an HTTP call to the sidecar removes the requirement for OrcaSlicer to be installed
on the Themis host and centralises all OrcaSlicer execution inside the sidecar container.
The existing two-tier retry (full 3MF → geometry-only) and Bambu `--export-3mf` path must
be preserved.

### User Story
As a **print farm operator**, I want Themis to dispatch slice jobs to the orca sidecar
over HTTP so that I do not need OrcaSlicer installed on the machine running Themis.

### Acceptance Criteria
1. `SlicerService._run()` (or an equivalent replacement) sends the prepared 3MF bytes to
   `POST /api/slice/prepared` using the client from Story 1. The `plate`, `export_3mf`,
   and `geometry_only_retry` parameters are forwarded correctly.
2. After submitting, Themis polls `GET /api/slice/status/{job_id}` at a configurable
   interval (default 2 s, env var `ORCA_POLL_INTERVAL_SECONDS`) until status is
   `completed` or `failed`. The existing `_SLICE_TIMEOUT` (600 s) is enforced as a
   wall-clock deadline across all poll attempts; timeout raises `SliceError`.
3. When the sidecar returns `status: failed`, Themis raises `SliceError` with the
   sidecar's `error` field as the message — matching the current behaviour where a
   non-zero subprocess exit raises `SliceError`.
4. The existing two-tier retry in `slicer_service.py:slice()` (`geometry_only=False` then
   `geometry_only=True`) is unchanged in logic; only the inner `_run()` call changes.
5. The Bambu printer path (`orca_export_args()` returning `["--export-3mf", <name>]`) is
   preserved: when `export_3mf` is set, the sidecar's `export_3mf` parameter is passed
   and Themis expects a `.3mf` output file from Story 3.
6. All existing pytest tests in `tests/` that cover slicing pass without modification. If
   any test previously mocked the subprocess directly, it is updated to mock
   `orca_sidecar_client.slice_prepared` instead.
7. `app/config.py:get_orca_executable()` and the subprocess import are no longer called
   from `slicer_service.py` after this change (they may still exist for thumbnail regen
   until Story 5 lands).

### Out of Scope
- SSE log streaming (polling is sufficient for this story)
- Downloading the output file (Story 3)
- Thumbnail regen (Story 5)

---

## Story 3 — Download Slice Output and Persist to Themis Storage

### Intent / Need
The sidecar evicts a job the moment its output is downloaded (`GET /api/slice/download`),
so the file must be fetched and saved to Themis's own `data/gcode/{job_id}/` directory
immediately after the job completes. Without this story, sliced output is never available
to the printer upload step.

### User Story
As a **print farm operator**, I want sliced gcode (or 3MF archive) to be saved to Themis
storage after the sidecar finishes, so that the printer upload step can access it as
before.

### Acceptance Criteria
1. After `poll_status` returns `completed`, Themis calls `client.download(job_id)` and
   writes the bytes to `{THEMIS_DATA_DIR}/gcode/{job_id}/{sliced_file}`, where
   `sliced_file` comes from the status response. The directory is created if absent.
2. The path returned from the slice operation matches what the queue engine and printer
   upload step currently expect (same path structure as the old `--outputdir` output).
3. If `download()` raises `SidecarError` (e.g. the job was already evicted due to a race),
   Themis raises `SliceError` with a message indicating the download failed, so the queue
   engine's existing failure handling takes over.
4. Gcode thumbnail injection (`_inject_thumbnail()`) continues to work unchanged: it reads
   the PNG from the **source** 3MF (which Themis still holds locally) and prepends it to
   the downloaded gcode file. No change to `_inject_thumbnail()` is required.
5. An integration test (using `httpx` mock or `respx`) submits a fake slice job, receives
   a `completed` status, and asserts the output file is written to the expected path with
   the correct bytes.
6. The Bambu path (`.gcode.3mf` output) is verified: when `export_3mf` was set, the
   downloaded file is saved with the `.gcode.3mf` extension and the queue engine's Bambu
   upload path can read it.

### Out of Scope
- Streaming download for large files (synchronous download is acceptable for now)
- Cleaning up old job directories (existing data-dir cleanup logic is unchanged)

---

## Story 4 — Add Thumbnail Endpoint to Orca Sidecar

### Intent / Need
`thumbnail_regen.py` uses `--arrange 0` to re-render plate thumbnails without disturbing
geometry layout. The sidecar currently always passes `--arrange 1`, so it cannot be used
for thumbnails as-is. This story adds a dedicated endpoint to the sidecar so that Story 5
can replace the local CLI call. **This story requires changes to `projects/orca`, not to
Themis.**

### User Story
As a **Themis developer**, I want the orca sidecar to expose a thumbnail-regen endpoint
that does not rearrange geometry, so that Themis can generate plate thumbnails through the
sidecar without altering part placement.

### Acceptance Criteria
1. A new endpoint `POST /api/slice/thumbnail` is added to the sidecar with form fields:
   - `file` (binary, 3MF) — the source file
   - `plate` (int, 1-based) — which plate to render
   The endpoint runs OrcaSlicer with `--slice {plate} --arrange 0 --export-3mf {out} {input}`
   (matching exactly what `thumbnail_regen.py` currently does locally).
2. On success the endpoint returns the extracted `Metadata/plate_{plate}.png` PNG bytes
   directly as `Content-Type: image/png`. If the plate index is renumbered by OrcaSlicer
   (single-plate export may output `plate_1.png` regardless of requested plate), the
   extraction logic tries `plate_{plate}.png` then falls back to `plate_1.png`.
3. On failure (OrcaSlicer non-zero exit, timeout, or no PNG extracted) the endpoint
   returns HTTP 422 with a JSON body `{ "error": "<reason>" }`.
4. The endpoint has a configurable timeout (default 120 s, matching Themis's current
   per-plate timeout). It does **not** use the async job system — it is synchronous and
   returns the PNG in the response body directly.
5. `GET /api/health` response includes a `thumbnail_endpoint` field set to `true` so
   Themis can detect sidecar versions that support this endpoint.
6. A test in `projects/orca` (using `pytest` + `httpx TestClient` or equivalent) submits
   a minimal valid 3MF fixture and asserts a PNG is returned with the correct magic bytes
   (`\x89PNG`).

### Out of Scope
- Async job tracking for thumbnail requests (synchronous response is intentional)
- Caching or deduplication of thumbnail requests

---

## Story 5 — Replace Thumbnail Regen Subprocess with Sidecar Call

### Intent / Need
`thumbnail_regen.py` currently spawns a local OrcaSlicer subprocess per plate. Replacing
it with a call to the sidecar endpoint from Story 4 removes the last direct CLI
dependency in Themis. Failure handling must remain graceful — a failed thumbnail regen
must never block the queue or corrupt the DB record.

### User Story
As a **print farm operator**, I want library thumbnails to be generated via the orca
sidecar so that Themis requires no local OrcaSlicer installation for any operation.

### Acceptance Criteria
1. `thumbnail_regen.py:_regen_sync()` is replaced (or refactored to async) to call
   `POST /api/slice/thumbnail` for each plate that lacks a cached thumbnail, using the
   client from Story 1.
2. The per-plate timeout (currently 120 s) is enforced on the HTTP call. A timeout or
   `SidecarError` for a single plate logs a `WARNING` and moves on to the next plate —
   identical to current behaviour.
3. The PNG bytes returned by the sidecar are written to
   `{THEMIS_DATA_DIR}/filecache/{file_id}/thumbnails/plate_{N}.png` and the DB record
   (`UploadedFile.plates[N].thumbnail_path`) is updated — identical to current behaviour.
4. If `GET /api/health` does not include `thumbnail_endpoint: true` (sidecar is an older
   version), Themis logs a one-time `WARNING` at startup and skips thumbnail regen
   entirely rather than erroring.
5. All existing pytest tests for thumbnail regen pass. Any test that previously mocked
   the OrcaSlicer subprocess is updated to mock `orca_sidecar_client` instead.
6. After this story, `app/config.py:get_orca_executable()` is no longer called from any
   service module. The getter may remain in config for backwards compatibility but is
   marked with a deprecation comment.
7. On Windows local dev (where the sidecar may not be running), missing thumbnails are
   skipped gracefully (covered by criterion 2 — `SidecarError` on connection refused is
   treated as a per-plate warning).

### Out of Scope
- Backfilling thumbnails for files uploaded before the sidecar was available (the
  existing `scripts/reextract_thumbnails.py` covers this use case)
- Changing when thumbnail regen is triggered (still fires as a background task on upload)

---

## Dependency Order

```
Story 1 (client + health gate)
    ├── Story 2 (slice via sidecar)
    │       └── Story 3 (download output)      ← Stories 2+3 ship together
    └── Story 4 (sidecar thumbnail endpoint)   ← sidecar-side change
            └── Story 5 (Themis thumbnail client)
```

Stories 2+3 are the critical path — they replace the slice subprocess and make the core
queue functional via the sidecar. Stories 4+5 can follow independently once Story 1 is
in place.
