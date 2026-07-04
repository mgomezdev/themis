# Handoff: Project Assemble Segfault (OrcaSlicer headless)

**Date:** 2026-07-03  
**Branch:** `feat/project-builder`  
**Status:** Workaround applied, root cause unresolved

---

## What breaks

`POST /api/v1/projects/{id}/assemble` always returns 502:

```
Orca sidecar error during assembly: arrange returned 400:
{"detail":"Slicer auto-arrange process failed (exit code 139)."}
```

Exit code 139 = segmentation fault.

---

## Root cause

OrcaSlicer's headless binary (`/opt/orcaslicer/bin/orca-slicer`, v2.3.2 AppImage-style bundle) crashes with a segfault whenever it tries to load a 3MF file — even with the minimal possible invocation:

```bash
xvfb-run -a orcaslicer --datadir /config --export-3mf /tmp/out.3mf /tmp/in.3mf
```

Tested with:
- `--arrange 1 --orient 1` → segfault
- `--arrange 1 --orient 0` → segfault
- `--arrange 0` (no flags) → segfault
- `LIBGL_ALWAYS_SOFTWARE=1` prefix → still segfaults
- `--info` on a simple STL → works fine (returns info, no crash)
- `--info` / `--export-3mf` on ANY 3MF → segfault

So the crash is **3MF-loading specific**, not arrange/orient specific. Something in OrcaSlicer's 3MF parser or its OpenGL init triggered by 3MF load crashes in the container environment.

The container has `xvfb-run`, libGL, and Mesa — but the Docker image runs on WSL2 (Windows 11, Docker Desktop 29.5.3). The GPU/driver stack is emulated and may be missing something OrcaSlicer's 3MF path requires.

---

## Current workaround

In `backend/app/api/routes/projects.py` line ~452:

```python
# Was: client.arrange(combined_path, True, True, 130.0)
arranged_bytes = await asyncio.to_thread(
    client.arrange, combined_path, False, False, 130.0
)
```

Both `arrange` and `orient` are `False` but the call still crashes because `--export-3mf` alone segfaults on any 3MF. **The workaround does not actually work** — `assemble` is still broken end-to-end.

---

## What does work

Direct slice via the Orca sidecar bypasses assemble entirely and works correctly:

```bash
curl -X POST http://localhost:5000/api/slice/start \
  -F "file=@model.stl" \
  -F "machine_uuid=410bd310-3561-578b-b9ec-81e439f68ec0" \
  -F "process_uuid=de49920c-3fce-501f-99ee-af930d79f1e0" \
  -F 'filament_uuids=["bcab178e-4ca7-510f-a0ed-97837d9d3c48"]' \
  -F "plate=1"
```

The sidecar's STL→slice path (`/api/slice/start`) converts the STL internally and slices successfully. This is what the queue engine uses when it claims a job.

---

## Fix options to investigate

### Option A: Skip the Orca arrange pass entirely
Instead of sending the combined 3MF through `client.arrange()`, just use the raw bytes from `ProjectPackBuilder` directly as the project file. The arrange step is cosmetic (plate layout optimisation) — slicing works without it.

In `projects.py`, replace the `arrange` call block with:
```python
arranged_bytes = combined_path.read_bytes()
```

Risk: models won't be auto-arranged on the plate (user gets whatever layout `ProjectPackBuilder` produces). Acceptable for now.

### Option B: Fix the OrcaSlicer container environment
Investigate why 3MF loading crashes:
- Test with `--debug 5` to get verbose logs before the crash
- Try running under `gdb` or `strace` to find the faulting instruction
- Check if the AppImage-extracted binary has unmet library deps (`ldd /opt/orcaslicer/bin/orca-slicer`)
- Try setting `MESA_GL_VERSION_OVERRIDE=3.3`, `GALLIUM_DRIVER=softpipe`, or disabling GPU init via env vars

### Option C: Use a different OrcaSlicer invocation for 3MF export
OrcaSlicer may have a non-GUI path for 3MF re-export that doesn't trigger the crash. Check if `--slice 0` or another flag avoids the crashing code path.

---

## Relevant files

| File | Purpose |
|------|---------|
| `backend/app/api/routes/projects.py` | `assemble_project()` — where `client.arrange()` is called |
| `backend/app/services/orca_sidecar_client.py` | `OrcaSidecarClient.arrange()` — POSTs to Orca `/api/arrange` |
| `orca/app/main.py` (separate repo) | `/api/arrange` endpoint — builds and runs the `orcaslicer` command |
| `docker-compose.dev.yml` | Publishes Orca port 5000 for direct sidecar testing |

---

## Quick test to reproduce

```powershell
# 1. Upload any STL
curl.exe -X POST http://localhost:8001/api/v1/files/upload -F "file=@cube.stl"
# → {"id": 1, ...}

# 2. Create a project
$body = '{"name":"test","machine_uuid":"410bd310-3561-578b-b9ec-81e439f68ec0","process_uuid":"de49920c-3fce-501f-99ee-af930d79f1e0"}'
Invoke-WebRequest http://localhost:8001/api/v1/projects -Method POST -Body $body -ContentType application/json
# → {"id": 1, ...}

# 3. Add item
$body = '{"file_id":1,"filament_profile_uuid":"bcab178e-4ca7-510f-a0ed-97837d9d3c48"}'
Invoke-WebRequest http://localhost:8001/api/v1/projects/1/items -Method POST -Body $body -ContentType application/json

# 4. Assemble — this crashes
Invoke-WebRequest http://localhost:8001/api/v1/projects/1/assemble -Method POST -Body '{}' -ContentType application/json
# → 502: "Slicer auto-arrange process failed (exit code 139)"
```

---

## Recommended first step

Try **Option A** first — it's a 3-line change and unblocks the full project→job flow immediately. The arrange step can be added back properly once the OrcaSlicer container environment issue is diagnosed.
