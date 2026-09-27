# Manual Job Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan
> task-by-task, executing directly in the main session — this project's CLAUDE.md "Development workflow"
> explicitly opts out of per-task subagent fan-out. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator mark a job "complete" from its detail page — running a real slice against a
chosen printer to get realistic filament/time usage and a real Spoolman deduction — without Themis ever
sending anything to the printer.

**Architecture:** A new `POST /api/v1/jobs/{id}/complete-manually` endpoint reuses the same isolated-slice
pattern `verify-slice` already uses (extracted into a shared helper so neither duplicates the
`SliceRequest`-building logic), then does the same DB bookkeeping a real completion does
(`handle_print_complete` in `queue_engine.py`) — actuals, printer wear counters, `awaiting_plate_clear`,
Spoolman deduction — minus ever touching the printer connection and minus webhooks/notifications. A new
button + confirm panel on `JobDetailScreen.tsx` drives it.

**Spec:** `docs/superpowers/specs/2026-09-15-manual-job-completion-design.md`

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async (backend), React + TypeScript + Vitest (frontend). No new
dependencies.

---

### Task 1: Extract `_build_slice_request` helper (refactor, no behavior change)

`verify_slice` (in `backend/app/api/routes/jobs.py`) builds a `SliceRequest` inline. Task 2 needs the
exact same construction. Extract it now so neither duplicates it — a pure, behavior-preserving
extraction, so no new test (the existing `verify_slice` tests are the regression check).

**Files:**
- Modify: `backend/app/api/routes/jobs.py` (the `verify_slice` function, currently around line 765-836)

- [ ] **Step 1: Run the existing verify-slice tests to record the baseline**

Run: `cd backend && pytest tests/api/test_jobs_api.py -k verify_slice -v`
Expected: PASS (4 tests: `test_verify_slice_success`, `test_verify_slice_slice_error`,
`test_verify_slice_missing_printer_config`, `test_verify_slice_does_not_touch_production_gcode_dir`)

- [ ] **Step 2: Extract the helper**

In `backend/app/api/routes/jobs.py`, find the `verify_slice` function. It currently reads (paraphrased
from the existing code — match against what's actually in the file, since line numbers may have
shifted):

```python
async def verify_slice(
    job_id: int,
    body: VerifySliceBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Test-slice without printing or modifying job state. Debug use only.
    Returns `{ok: true}` on success or `{ok: false, error: "..."}` on failure."""
    job = await _get_or_404(job_id, session)

    printer = await session.get(Printer, body.printer_id)
    if printer is None:
        raise HTTPException(404, f"Printer {body.printer_id} not found")

    cfg_result = await session.execute(
        select(JobPrinterConfig).where(
            JobPrinterConfig.job_id == job_id,
            JobPrinterConfig.printer_id == body.printer_id,
        )
    )
    config = cfg_result.scalar_one_or_none()
    if config is None:
        raise HTTPException(404, f"Job {job_id} has no config for printer {body.printer_id}")

    uploaded_file = await session.get(UploadedFile, job.uploaded_file_id)
    if uploaded_file is None:
        raise HTTPException(404, f"File {job.uploaded_file_id} not found")

    if not printer.current_orca_printer_profile:
        return {"ok": False, "error": "Printer has no OrcaSlicer machine preset configured"}

    # Mirror _run_slice_and_print: resolve the filament slot and build the SliceRequest.
    loaded = printer.loaded_filaments or []
    slot = _slot_for_config(config, loaded)
    filament_profile = config.filament_profile or (slot or {}).get("filament_profile") or None

    stem = os.path.splitext(os.path.basename(uploaded_file.original_filename or "model"))[0]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "model"
    file_base = f"{safe}_p{job.plate_number}_j{job_id}"

    client = printer_manager._clients.get(body.printer_id)
    export_args = client.orca_export_args(file_base) if client else []

    cfg_tool_index = config.tool_index
    cfg_filament_map = config.filament_map
    prepare_hook = None
    if client is not None and (cfg_tool_index is not None or cfg_filament_map):
        prepare_hook = (
            lambda p, c=client, ti=cfg_tool_index, fm=cfg_filament_map:
            c.remap_sliceable_3mf(p, tool_index=ti, filament_map=fm)
        )

    if cfg_filament_map:
        ordered = sorted(loaded, key=lambda s: s.get("slot", 0))
        filament_presets = [s.get("filament_profile") for s in ordered if s.get("filament_profile")]
    else:
        filament_presets = [filament_profile] if filament_profile else []

    plate_config = {"curr_bed_type": printer.build_plate_type} if printer.build_plate_type else {}
    plate_config.update(job.overrides or {})
    req = SliceRequest(
        job_id=job_id,
        source_3mf=str(library_abs_path(app_config.get_library_dir(), uploaded_file.relative_path)),
        plate_number=job.plate_number,
        machine_preset=printer.current_orca_printer_profile,
        process_preset=config.print_profile,
        filament_presets=filament_presets,
        filament_colours=[config.filament_color] if config.filament_color else [],
        export_args=export_args,
        prepare_hook=prepare_hook,
        extra_config=plate_config,
    )

    # Isolated from the production gcode dir (<data_dir>/gcode/<job_id>) — slice()
    # unlinks *.gcode/*.gcode.3mf in its output dir before writing, which would
    # otherwise destroy a parked job's already-produced production artifact.
    output_dir = queue_engine._slicer._data_dir / "gcode_verify" / str(job_id)
    try:
        # Routed through the same serialized _slice_queue as production/estimate
        # slices (lowest priority) rather than queue_engine._executor directly —
        # that pool is also what upload_file/start_print depend on, and a
        # debug-only test-slice can block for minutes.
        await queue_engine.run_verify_slice(req, output_dir)
        return {"ok": True, "error": None}
    except SliceError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected error in verify-slice for job %s", job_id)
        return {"ok": False, "error": f"Unexpected error: {exc}"}
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)
```

Replace it with a module-level helper (place it right before the `verify_slice` function) plus a
shortened `verify_slice` that calls it:

```python
def _build_slice_request(
    job: Job, config: JobPrinterConfig, printer: Printer, uploaded_file: UploadedFile,
) -> SliceRequest:
    """Build the SliceRequest for a debug/manual slice of `job` against `printer`'s
    matched `config` - shared by verify-slice and complete-manually, both of which
    slice in an isolated directory and never touch the job's production gcode path."""
    loaded = printer.loaded_filaments or []
    slot = _slot_for_config(config, loaded)
    filament_profile = config.filament_profile or (slot or {}).get("filament_profile") or None

    stem = os.path.splitext(os.path.basename(uploaded_file.original_filename or "model"))[0]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "model"
    file_base = f"{safe}_p{job.plate_number}_j{job.id}"

    client = printer_manager._clients.get(printer.id)
    export_args = client.orca_export_args(file_base) if client else []

    cfg_tool_index = config.tool_index
    cfg_filament_map = config.filament_map
    prepare_hook = None
    if client is not None and (cfg_tool_index is not None or cfg_filament_map):
        prepare_hook = (
            lambda p, c=client, ti=cfg_tool_index, fm=cfg_filament_map:
            c.remap_sliceable_3mf(p, tool_index=ti, filament_map=fm)
        )

    if cfg_filament_map:
        ordered = sorted(loaded, key=lambda s: s.get("slot", 0))
        filament_presets = [s.get("filament_profile") for s in ordered if s.get("filament_profile")]
    else:
        filament_presets = [filament_profile] if filament_profile else []

    plate_config = {"curr_bed_type": printer.build_plate_type} if printer.build_plate_type else {}
    plate_config.update(job.overrides or {})
    return SliceRequest(
        job_id=job.id,
        source_3mf=str(library_abs_path(app_config.get_library_dir(), uploaded_file.relative_path)),
        plate_number=job.plate_number,
        machine_preset=printer.current_orca_printer_profile,
        process_preset=config.print_profile,
        filament_presets=filament_presets,
        filament_colours=[config.filament_color] if config.filament_color else [],
        export_args=export_args,
        prepare_hook=prepare_hook,
        extra_config=plate_config,
    )


async def verify_slice(
    job_id: int,
    body: VerifySliceBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Test-slice without printing or modifying job state. Debug use only.
    Returns `{ok: true}` on success or `{ok: false, error: "..."}` on failure."""
    job = await _get_or_404(job_id, session)

    printer = await session.get(Printer, body.printer_id)
    if printer is None:
        raise HTTPException(404, f"Printer {body.printer_id} not found")

    cfg_result = await session.execute(
        select(JobPrinterConfig).where(
            JobPrinterConfig.job_id == job_id,
            JobPrinterConfig.printer_id == body.printer_id,
        )
    )
    config = cfg_result.scalar_one_or_none()
    if config is None:
        raise HTTPException(404, f"Job {job_id} has no config for printer {body.printer_id}")

    uploaded_file = await session.get(UploadedFile, job.uploaded_file_id)
    if uploaded_file is None:
        raise HTTPException(404, f"File {job.uploaded_file_id} not found")

    if not printer.current_orca_printer_profile:
        return {"ok": False, "error": "Printer has no OrcaSlicer machine preset configured"}

    req = _build_slice_request(job, config, printer, uploaded_file)

    # Isolated from the production gcode dir (<data_dir>/gcode/<job_id>) — slice()
    # unlinks *.gcode/*.gcode.3mf in its output dir before writing, which would
    # otherwise destroy a parked job's already-produced production artifact.
    output_dir = queue_engine._slicer._data_dir / "gcode_verify" / str(job_id)
    try:
        # Routed through the same serialized _slice_queue as production/estimate
        # slices (lowest priority) rather than queue_engine._executor directly —
        # that pool is also what upload_file/start_print depend on, and a
        # debug-only test-slice can block for minutes.
        await queue_engine.run_verify_slice(req, output_dir)
        return {"ok": True, "error": None}
    except SliceError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected error in verify-slice for job %s", job_id)
        return {"ok": False, "error": f"Unexpected error: {exc}"}
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)
```

- [ ] **Step 3: Run the verify-slice tests again to confirm no regression**

Run: `cd backend && pytest tests/api/test_jobs_api.py -k verify_slice -v`
Expected: PASS — same 4 tests, unchanged behavior.

- [ ] **Step 4: Run the full backend suite**

Run: `cd backend && pytest -v`
Expected: PASS, no failures.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/jobs.py
git commit -m "Extract _build_slice_request from verify_slice for reuse by complete-manually"
```

---

### Task 2: `POST /api/v1/jobs/{id}/complete-manually` — happy path

**Files:**
- Modify: `backend/app/api/routes/jobs.py` (add import, `CompleteManuallyBody`, the new route — place it
  right after the `verify_slice` function so the two isolated-slice endpoints sit together)
- Test: Create `backend/tests/api/test_complete_manually.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/api/test_complete_manually.py`:

```python
import io
import json
import zipfile
from unittest.mock import MagicMock, patch

import pytest


def _make_3mf() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/slice_info.config", json.dumps({
            "plate": [{"index": 1, "prediction": 60, "weight": [5.0]}]
        }))
        zf.writestr("Metadata/plate_1.png", b"\x89PNG")
    return buf.getvalue()


async def _upload_file(client, tmp_path):
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"):
        (tmp_path / "library").mkdir(exist_ok=True)
        (tmp_path / "filecache").mkdir(exist_ok=True)
        resp = await client.post(
            "/api/v1/files/upload",
            files={"file": ("m.3mf", _make_3mf(), "application/octet-stream")},
        )
    return resp.json()["id"]


async def _create_printer(client, **overrides):
    body = {
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4",
    }
    body.update(overrides)
    resp = await client.post("/api/v1/printers", json=body)
    return resp.json()["id"]


async def _create_job(client, file_id, printer_id, **config_overrides):
    config = {
        "printer_id": printer_id, "print_profile": "0.20mm",
        "filament_type": "any", "filament_color": "any",
    }
    config.update(config_overrides)
    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [config],
        })
    return resp.json()["id"]


def _fake_gcode_with_estimates(output_dir, grams: float = 12.5, time_str: str = "1h 5m 30s"):
    output_dir.mkdir(parents=True, exist_ok=True)
    gcode_file = output_dir / "out.gcode"
    gcode_file.write_text(
        f"; filament used [g] = {grams}\n"
        f"; estimated printing time (normal mode) = {time_str}\n"
    )
    return str(gcode_file)


async def test_complete_manually_happy_path(client, tmp_path):
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    job_id = await _create_job(client, file_id, printer_id)

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path

    async def fake_run_verify_slice(req, output_dir):
        return _fake_gcode_with_estimates(output_dir)

    mock_qe.run_verify_slice = fake_run_verify_slice

    with patch("app.api.routes.jobs.queue_engine", mock_qe), \
         patch("app.api.routes.jobs._deduct_spool") as mock_deduct:
        resp = await client.post(
            f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "complete"
    assert body["completed_at"] is not None
    assert body["actual_filament_grams"] == 12.5
    assert body["actual_seconds"] == 3930  # 1h5m30s
    assert body["assigned_printer_id"] == printer_id
    mock_deduct.assert_not_called()  # no spool loaded — nothing to deduct

    printer_resp = await client.get(f"/api/v1/printers/{printer_id}")
    printer_data = printer_resp.json()
    assert printer_data["lifetime_job_count"] == 1
    assert printer_data["lifetime_print_seconds"] == 3930
    assert printer_data["awaiting_plate_clear"] is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && pytest tests/api/test_complete_manually.py -v`
Expected: FAIL — `404 Not Found` (the route doesn't exist yet; check `/api/v1/printers/{id}` exists as a
GET route already — if that specific assertion errors differently, it's still a pre-existing route, the
failure will be on the `complete-manually` call itself).

- [ ] **Step 3: Add the import and request body model**

In `backend/app/api/routes/jobs.py`, extend the existing queue_engine import line:

```python
from ...services.queue_engine import queue_engine, _slot_for_config
```

to:

```python
from ...services.queue_engine import queue_engine, _slot_for_config, _parse_gcode_estimates, _deduct_spool
```

Then add the request body model near `VerifySliceBody` (find that class and add this right after it):

```python
class CompleteManuallyBody(BaseModel):
    printer_id: int
```

- [ ] **Step 4: Add the route**

In `backend/app/api/routes/jobs.py`, immediately after the `verify_slice` function (from Task 1), add:

```python
_MANUAL_COMPLETE_TERMINAL_STATUSES = {"complete", "cancelled", "failed"}


@router.post(
    "/{job_id}/complete-manually",
    summary="Manually complete a job without printing it",
    responses={
        404: {"description": "Job, printer, or printer config not found"},
        409: {"description": "Job is already in a terminal status"},
        422: {"description": "Printer has no OrcaSlicer machine preset configured"},
    },
    dependencies=[Depends(require_scope("jobs:write"))],
)
async def complete_job_manually(
    job_id: int,
    body: CompleteManuallyBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Slice the job for the chosen printer - a real slice, in an isolated directory,
    never the job's production gcode path - and mark it complete without ever
    printing it or sending anything to the printer. For work that was already done
    physically: printed before Themis tracked it, printed manually, or a job
    Themis's own tracking is stuck on but which really did finish. Deducts Spoolman
    filament on success. Fires no webhooks or notifications either way - this is an
    out-of-band admin action, not a real completion or a real failure as far as
    integrations are concerned."""
    job = await _get_or_404(job_id, session)
    if job.status in _MANUAL_COMPLETE_TERMINAL_STATUSES:
        raise HTTPException(409, f"Job in status {job.status!r} is already terminal")

    printer = await session.get(Printer, body.printer_id)
    if printer is None:
        raise HTTPException(404, f"Printer {body.printer_id} not found")

    cfg_result = await session.execute(
        select(JobPrinterConfig).where(
            JobPrinterConfig.job_id == job_id,
            JobPrinterConfig.printer_id == body.printer_id,
        )
    )
    config = cfg_result.scalar_one_or_none()
    if config is None:
        raise HTTPException(404, f"Job {job_id} has no config for printer {body.printer_id}")

    uploaded_file = await session.get(UploadedFile, job.uploaded_file_id)
    if uploaded_file is None:
        raise HTTPException(404, f"File {job.uploaded_file_id} not found")

    if not printer.current_orca_printer_profile:
        raise HTTPException(422, "Printer has no OrcaSlicer machine preset configured")

    job.status = "slicing"
    job.assigned_printer_id = body.printer_id
    job.block_reason = None
    job.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()

    req = _build_slice_request(job, config, printer, uploaded_file)
    output_dir = queue_engine._slicer._data_dir / "gcode_manual_complete" / str(job_id)
    try:
        gcode_path = await queue_engine.run_verify_slice(req, output_dir)
    except SliceError as exc:
        config.slice_failed = True
        config.slice_error = str(exc)
        job.status = "blocked"
        job.block_reason = f"slicing failed: {exc}"
        job.assigned_printer_id = None
        job.updated_at = datetime.now(timezone.utc).isoformat()
        await session.commit()
        await session.refresh(job)
        return _to_dict(job)
    except Exception as exc:
        logger.exception("Unexpected error in complete-manually slice for job %s", job_id)
        config.slice_failed = True
        config.slice_error = f"Unexpected error: {exc}"
        job.status = "blocked"
        job.block_reason = f"slicing failed: Unexpected error: {exc}"
        job.assigned_printer_id = None
        job.updated_at = datetime.now(timezone.utc).isoformat()
        await session.commit()
        await session.refresh(job)
        return _to_dict(job)
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)

    grams, secs, extruder_grams = _parse_gcode_estimates(gcode_path)
    job.actual_filament_grams = grams
    job.actual_seconds = secs
    if extruder_grams is not None:
        job.actual_filament_breakdown = [
            {
                "extruder_index": i,
                "filament_profile": req.filament_presets[i] if i < len(req.filament_presets) else None,
                "grams": g,
            }
            for i, g in enumerate(extruder_grams)
        ]
    job.status = "complete"
    job.completed_at = datetime.now(timezone.utc).isoformat()
    job.outcome = None
    job.updated_at = datetime.now(timezone.utc).isoformat()

    printer.lifetime_job_count += 1
    printer.lifetime_print_seconds += secs or 0
    printer.awaiting_plate_clear = True
    printer_manager.set_awaiting_plate_clear(body.printer_id, True)

    spool_id = None
    spoolman_url = None
    spoolman_key = None
    grams_to_deduct = None
    if grams is not None:
        spoolman_cfg = await session.get(SpoolmanConfig, 1)
        if spoolman_cfg and spoolman_cfg.enabled and spoolman_cfg.url:
            loaded = printer.loaded_filaments or []
            slot = _slot_for_config(config, loaded)
            if slot is not None:
                raw_spool_id = slot.get("spoolman_spool_id")
                if raw_spool_id is not None:
                    try:
                        spool_id = int(raw_spool_id)
                        spoolman_url = spoolman_cfg.url
                        spoolman_key = spoolman_cfg.api_key
                        grams_to_deduct = grams
                        job.deduction_skipped = False
                    except (TypeError, ValueError):
                        logger.warning(
                            "Invalid spoolman_spool_id %r for job %s — deduction skipped",
                            raw_spool_id, job_id,
                        )

    await session.commit()
    await session.refresh(job)

    if spool_id is not None and spoolman_url and grams_to_deduct is not None:
        asyncio.create_task(_deduct_spool(spoolman_url, spoolman_key, spool_id, grams_to_deduct))

    return _to_dict(job)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && pytest tests/api/test_complete_manually.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/jobs.py backend/tests/api/test_complete_manually.py
git commit -m "Add POST /api/v1/jobs/{id}/complete-manually happy path"
```

---

### Task 3: Validation and failure-path tests

**Files:**
- Test: `backend/tests/api/test_complete_manually.py` (append)

The route code for these cases already exists from Task 2 — this task is pure test coverage for paths
not yet exercised.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/api/test_complete_manually.py`:

```python
async def test_complete_manually_404_unknown_job(client):
    resp = await client.post("/api/v1/jobs/999999/complete-manually", json={"printer_id": 1})
    assert resp.status_code == 404


async def test_complete_manually_404_unknown_printer(client, tmp_path):
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    job_id = await _create_job(client, file_id, printer_id)

    resp = await client.post(
        f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": 999999},
    )
    assert resp.status_code == 404


async def test_complete_manually_404_no_config_for_printer(client, tmp_path):
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    other_printer_id = await _create_printer(client, name="P1S #2")
    job_id = await _create_job(client, file_id, printer_id)

    resp = await client.post(
        f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": other_printer_id},
    )
    assert resp.status_code == 404


async def test_complete_manually_409_already_complete(client, tmp_path):
    from app.database import get_session
    from app.main import app
    from app.models import Job

    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    job_id = await _create_job(client, file_id, printer_id)

    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    job = await session.get(Job, job_id)
    job.status = "complete"
    await session.commit()
    await agen.aclose()

    resp = await client.post(
        f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
    )
    assert resp.status_code == 409


async def test_complete_manually_slice_failure_blocks_job(client, tmp_path):
    from app.services.slicer_service import SliceError

    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    job_id = await _create_job(client, file_id, printer_id)

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path

    async def fake_run_verify_slice(req, output_dir):
        raise SliceError("OrcaSlicer exited with code 1\nboom")

    mock_qe.run_verify_slice = fake_run_verify_slice

    with patch("app.api.routes.jobs.queue_engine", mock_qe):
        resp = await client.post(
            f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "blocked"
    assert body["assigned_printer_id"] is None

    # _to_dict() (what complete-manually returns) doesn't carry block_reason -
    # only /jobs/{id}/details does. Check the real error landed there.
    details_resp = await client.get(f"/api/v1/jobs/{job_id}/details")
    assert "OrcaSlicer" in details_resp.json()["block_reason"]

    failures_resp = await client.get(f"/api/v1/jobs/{job_id}/slice-failures")
    failures = failures_resp.json()
    assert any(f["printer_id"] == printer_id and f["slice_failed"] for f in failures)


async def test_complete_manually_output_dir_cleaned_up_on_success(client, tmp_path):
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    job_id = await _create_job(client, file_id, printer_id)

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path
    expected_output_dir = tmp_path / "gcode_manual_complete" / str(job_id)

    async def fake_run_verify_slice(req, output_dir):
        assert output_dir == expected_output_dir
        return _fake_gcode_with_estimates(output_dir)

    mock_qe.run_verify_slice = fake_run_verify_slice

    with patch("app.api.routes.jobs.queue_engine", mock_qe), \
         patch("app.api.routes.jobs._deduct_spool"):
        resp = await client.post(
            f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
        )

    assert resp.status_code == 200
    assert not expected_output_dir.exists()


async def test_complete_manually_sends_no_printer_commands(client, tmp_path):
    """The whole point of this endpoint is that it never talks to the printer.
    A mock vendor client is wired into printer_manager so any accidental call to
    start_print/upload_file/stop_print (or anything else on it) fails loudly."""
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    job_id = await _create_job(client, file_id, printer_id)

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path

    async def fake_run_verify_slice(req, output_dir):
        return _fake_gcode_with_estimates(output_dir)

    mock_qe.run_verify_slice = fake_run_verify_slice

    mock_client = MagicMock()
    mock_client.connected = True
    from app.services.printer_manager import printer_manager
    printer_manager._clients[printer_id] = mock_client
    try:
        with patch("app.api.routes.jobs.queue_engine", mock_qe), \
             patch("app.api.routes.jobs._deduct_spool"):
            resp = await client.post(
                f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
            )
        assert resp.status_code == 200
        mock_client.start_print.assert_not_called()
        mock_client.upload_file.assert_not_called()
        mock_client.stop_print.assert_not_called()
        # orca_export_args (local file-naming, no printer I/O) IS expected to be
        # called by _build_slice_request - that's not a printer command.
    finally:
        printer_manager._clients.pop(printer_id, None)
```

Note: `test_complete_manually_output_dir_cleaned_up_on_success` also confirms the isolated directory is
`gcode_manual_complete/<job_id>`, distinct from `verify-slice`'s `gcode_verify/<job_id>` and from the
production `gcode/<job_id>` dir — three separate isolated spaces so none can ever collide with another.

- [ ] **Step 2: Run the tests to verify they pass**

Run: `cd backend && pytest tests/api/test_complete_manually.py -v`
Expected: PASS (all tests so far — 1 from Task 2's happy path plus these 7)

- [ ] **Step 3: Run the full backend suite**

Run: `cd backend && pytest -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/tests/api/test_complete_manually.py
git commit -m "Add validation and slice-failure tests for complete-manually"
```

---

### Task 4: Spoolman deduction test

**Files:**
- Test: `backend/tests/api/test_complete_manually.py` (append)

The deduction code already exists from Task 2 (it just never fires in the happy-path test because no
spool is loaded there). This task adds the case where it should fire, and confirms it's skipped when
Spoolman is disabled.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/api/test_complete_manually.py`:

```python
async def test_complete_manually_deducts_spoolman_filament(client, tmp_path):
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(
        client, loaded_filaments=[{"slot": 0, "type": "PLA", "color": "#000000", "spoolman_spool_id": 42}],
    )
    job_id = await _create_job(client, file_id, printer_id)

    await client.put("/api/v1/settings/spoolman", json={
        "enabled": True, "url": "http://spoolman.test",
    })

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path

    async def fake_run_verify_slice(req, output_dir):
        return _fake_gcode_with_estimates(output_dir, grams=8.0)

    mock_qe.run_verify_slice = fake_run_verify_slice

    with patch("app.api.routes.jobs.queue_engine", mock_qe), \
         patch("app.api.routes.jobs._deduct_spool") as mock_deduct:
        resp = await client.post(
            f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
        )

    assert resp.status_code == 200
    mock_deduct.assert_called_once()
    call_args = mock_deduct.call_args[0]
    assert call_args[0] == "http://spoolman.test"  # url
    assert call_args[2] == 42                       # spool_id
    assert call_args[3] == 8.0                       # grams


async def test_complete_manually_skips_deduction_when_spoolman_disabled(client, tmp_path):
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(
        client, loaded_filaments=[{"slot": 0, "type": "PLA", "color": "#000000", "spoolman_spool_id": 42}],
    )
    job_id = await _create_job(client, file_id, printer_id)
    # Spoolman left at its default (disabled).

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path

    async def fake_run_verify_slice(req, output_dir):
        return _fake_gcode_with_estimates(output_dir, grams=8.0)

    mock_qe.run_verify_slice = fake_run_verify_slice

    with patch("app.api.routes.jobs.queue_engine", mock_qe), \
         patch("app.api.routes.jobs._deduct_spool") as mock_deduct:
        resp = await client.post(
            f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
        )

    assert resp.status_code == 200
    mock_deduct.assert_not_called()
```

Check `_create_printer`'s signature (defined in Task 2) accepts arbitrary overrides via `**overrides` —
`loaded_filaments` merges into the POST body the same way `current_orca_printer_profile` does, so no
change is needed there.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && pytest tests/api/test_complete_manually.py -k deducts_spoolman -v`
Expected: FAIL if `_create_printer` doesn't yet accept a `loaded_filaments` override that actually
persists — check `POST /api/v1/printers`'s request schema accepts `loaded_filaments` in the body (it's a
plain JSON column per `Printer.loaded_filaments`, so this should already work via the generic printer
creation route without any code change; if the test fails for a different reason than an assertion
mismatch, re-check the printer-creation payload shape against `backend/app/api/routes/printers.py`'s
create schema).

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd backend && pytest tests/api/test_complete_manually.py -v`
Expected: PASS (all tests)

- [ ] **Step 4: Run the full backend suite**

Run: `cd backend && pytest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tests/api/test_complete_manually.py
git commit -m "Add Spoolman deduction tests for complete-manually"
```

---

### Task 5: Frontend API client function

**Files:**
- Modify: `frontend/src/api/queue.ts`

- [ ] **Step 1: Add the function**

In `frontend/src/api/queue.ts`, immediately after `verifySlice` (added in an earlier feature, currently
around line 271-280), add:

```typescript
export async function completeJobManually(
  jobId: number,
  printerId: number,
): Promise<ApiJob> {
  return request(`/api/v1/jobs/${jobId}/complete-manually`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ printer_id: printerId }),
  });
}
```

- [ ] **Step 2: Verify the frontend builds**

Run: `cd frontend && npm run build`
Expected: success, no TypeScript errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/queue.ts
git commit -m "Add completeJobManually frontend API client"
```

---

### Task 6: "Mark as already completed" button and confirm panel

**Files:**
- Modify: `frontend/src/screens/JobDetailScreen.tsx`
- Test: `frontend/src/screens/JobDetailScreen.test.tsx` (append)

- [ ] **Step 1: Write the failing tests**

In `frontend/src/screens/JobDetailScreen.test.tsx`, update the `vi.mock('../api/queue', ...)` block
(currently near the top of the file) to also mock the new function:

```typescript
vi.mock('../api/queue', async (importOriginal) => {
  const actual = await importOriginal<typeof queueApi>();
  return {
    ...actual,
    getJobDetails: vi.fn(),
    cancelJob: vi.fn(),
    unblockJob: vi.fn(),
    completeJobManually: vi.fn(),
  };
});
```

Then append these tests at the end of the file:

```typescript
describe('JobDetailScreen — manual completion', () => {
  it('shows the button for a queued job', async () => {
    vi.mocked(queueApi.getJobDetails).mockResolvedValue({ ...BASE_JOB, status: 'queued' });
    renderJobDetail();
    expect(await screen.findByRole('button', { name: /mark as already completed/i })).toBeTruthy();
  });

  it('does not show the button for a completed job', async () => {
    vi.mocked(queueApi.getJobDetails).mockResolvedValue({ ...BASE_JOB, status: 'complete' });
    renderJobDetail();
    await screen.findByText('part.3mf');
    expect(screen.queryByRole('button', { name: /mark as already completed/i })).toBeNull();
  });

  it('requires confirmation before calling the API', async () => {
    const user = userEvent.setup();
    vi.mocked(queueApi.getJobDetails).mockResolvedValue({ ...BASE_JOB, status: 'queued' });
    renderJobDetail();

    await user.click(await screen.findByRole('button', { name: /mark as already completed/i }));
    expect(queueApi.completeJobManually).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /^confirm$/i })).toBeTruthy();
  });

  it('pre-selects the single eligible printer and calls the API on confirm', async () => {
    const user = userEvent.setup();
    vi.mocked(queueApi.getJobDetails).mockResolvedValue({ ...BASE_JOB, status: 'queued' });
    vi.mocked(queueApi.completeJobManually).mockResolvedValue({ ...BASE_JOB, status: 'complete' } as queueApi.ApiJob);
    renderJobDetail();

    await user.click(await screen.findByRole('button', { name: /mark as already completed/i }));
    await user.click(screen.getByRole('button', { name: /^confirm$/i }));

    // completeJobManually is called, then handleCompleteManually awaits a
    // getJobDetails() refresh before the confirm panel closes — wait for that
    // full chain to settle rather than asserting immediately after the click.
    await waitFor(() => {
      expect(queueApi.completeJobManually).toHaveBeenCalledWith(5, 3); // job id 5, the sole printer_configs entry (printer_id 3)
    });
  });

  it('shows a printer picker when more than one config exists', async () => {
    const user = userEvent.setup();
    const job: queueApi.ApiJobDetails = {
      ...BASE_JOB,
      status: 'queued',
      printer_configs: [
        { ...BASE_JOB.printer_configs[0], printer_id: 3, printer_name: 'U1' },
        { ...BASE_JOB.printer_configs[0], printer_id: 4, printer_name: 'U2' },
      ],
    };
    vi.mocked(queueApi.getJobDetails).mockResolvedValue(job);
    renderJobDetail();

    await user.click(await screen.findByRole('button', { name: /mark as already completed/i }));
    expect(screen.getByText('U1')).toBeTruthy();
    expect(screen.getByText('U2')).toBeTruthy();
  });

  it('shows an error message when the API call fails', async () => {
    const user = userEvent.setup();
    vi.mocked(queueApi.getJobDetails).mockResolvedValue({ ...BASE_JOB, status: 'queued' });
    vi.mocked(queueApi.completeJobManually).mockRejectedValue(new Error('500 slice error'));
    renderJobDetail();

    await user.click(await screen.findByRole('button', { name: /mark as already completed/i }));
    await user.click(screen.getByRole('button', { name: /^confirm$/i }));

    expect(await screen.findByText(/500 slice error/i)).toBeTruthy();
  });
});
```

This test file doesn't currently import `userEvent` or `waitFor` — update its existing imports:

```typescript
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
```

(the file's current top import is `import { render, screen } from '@testing-library/react';` — add
`waitFor` to that same line rather than a separate one).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/screens/JobDetailScreen.test.tsx`
Expected: FAIL — no "Mark as already completed" button exists yet.

- [ ] **Step 3: Add the button, state, and confirm panel**

In `frontend/src/screens/JobDetailScreen.tsx`, update the import line to add the new API function:

```typescript
import { getJobDetails, cancelJob, unblockJob, completeJobManually, plateThumbnailUrl, type ApiJobDetails, type ApiJobPrinterConfig } from '../api/queue';
```

Add state alongside the existing `cancelling`/`unblocking` state (near line 106):

```typescript
  const [cancelling, setCancelling] = useState(false);
  const [unblocking, setUnblocking] = useState(false);
  const [showCompletePanel, setShowCompletePanel] = useState(false);
  const [completePrinterId, setCompletePrinterId] = useState<number | null>(null);
  const [completing, setCompleting] = useState(false);
  const [completeError, setCompleteError] = useState<string | null>(null);
```

Add a handler alongside `handleCancel`/`handleUnblock` (after `handleCancel`, around line 142):

```typescript
  function openCompletePanel() {
    if (!job) return;
    setCompleteError(null);
    setCompletePrinterId(
      job.assigned_printer?.id
      ?? (job.printer_configs.length === 1 ? job.printer_configs[0].printer_id : null),
    );
    setShowCompletePanel(true);
  }

  async function handleCompleteManually() {
    if (!job || completePrinterId == null || completing) return;
    setCompleting(true);
    setCompleteError(null);
    try {
      await completeJobManually(job.id, completePrinterId);
      const refreshed = await getJobDetails(job.id);
      setJob(refreshed);
      setShowCompletePanel(false);
    } catch (e) {
      setCompleteError(e instanceof Error ? e.message : String(e));
    } finally {
      setCompleting(false);
    }
  }
```

Add a derived boolean alongside `isActive`/`isBlocked`/`cancellable` (near line 167):

```typescript
  const canCompleteManually = !['complete', 'cancelled', 'failed'].includes(job.status);
```

Add the button + confirm panel as a new card, right before the `cancellable` block found in Task
exploration (around line 438 in the current file — search for `{cancellable && (` and insert this
immediately before it):

```typescript
          {canCompleteManually && (
            <div className="card" style={{ padding: 18 }}>
              {!showCompletePanel ? (
                <button
                  className="btn sm"
                  style={{ width: '100%' }}
                  onClick={openCompletePanel}
                >
                  {Icons.check} Mark as already completed
                </button>
              ) : (
                <div className="col" style={{ gap: 10 }}>
                  <div style={{ fontSize: 13, color: 'var(--warn)' }}>
                    This will mark the job complete without actually printing it — this can't be undone.
                  </div>
                  {job.printer_configs.length > 1 && (
                    <select
                      className="input"
                      value={completePrinterId ?? ''}
                      onChange={e => setCompletePrinterId(Number(e.target.value))}
                    >
                      <option value="" disabled>Choose a printer…</option>
                      {job.printer_configs.map(cfg => (
                        <option key={cfg.printer_id} value={cfg.printer_id}>{cfg.printer_name}</option>
                      ))}
                    </select>
                  )}
                  {completeError && (
                    <div style={{ fontSize: 12, color: 'var(--err)' }}>{completeError}</div>
                  )}
                  <div className="row gap-2">
                    <button
                      className="btn primary sm"
                      style={{ flex: 1 }}
                      disabled={completing || completePrinterId == null}
                      onClick={handleCompleteManually}
                    >
                      {completing ? 'Slicing…' : 'Confirm'}
                    </button>
                    <button
                      className="btn sm"
                      style={{ flex: 1 }}
                      disabled={completing}
                      onClick={() => setShowCompletePanel(false)}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/screens/JobDetailScreen.test.tsx`
Expected: PASS (all tests, including the pre-existing `low_stock_warning` ones)

- [ ] **Step 5: Verify the frontend builds**

Run: `cd frontend && npm run build`
Expected: success, no TypeScript errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/screens/JobDetailScreen.tsx frontend/src/screens/JobDetailScreen.test.tsx
git commit -m "Add 'Mark as already completed' button with confirm panel to JobDetailScreen"
```

---

### Task 7: Docs

**Files:**
- Modify: `docs/agent/data-model.md` (the `jobs` section)
- Modify: `docs/agent/backend-review.md` (note the new isolated-slice-directory convention)

- [ ] **Step 1: Document the new endpoint in data-model.md**

Open `docs/agent/data-model.md` and find the `### jobs` section (currently starting around line 88).
Find this exact line:

```markdown
- status enum: `queued|slicing|uploading|printing|paused|complete|blocked|failed|cancelled`.
```

Add a new bullet immediately after it:

```markdown
- status enum: `queued|slicing|uploading|printing|paused|complete|blocked|failed|cancelled`.
- `POST /api/v1/jobs/{id}/complete-manually`: slices for a chosen printer (real slice, isolated
  directory, same pattern as `verify-slice`) then marks the job `complete` without ever printing it -
  sets `actual_filament_grams`/`actual_seconds` from the real slice, deducts Spoolman filament, and
  updates the printer's `awaiting_plate_clear`/lifetime counters as if it had really finished, all
  without sending anything to the printer connection. Fires no webhooks/notifications either way. Works
  from any non-terminal status, including `printing`/`uploading`. See
  `docs/superpowers/specs/2026-09-15-manual-job-completion-design.md`.
```

- [ ] **Step 2: Flag the isolated-slice-directory pattern in backend-review.md**

Open `docs/agent/backend-review.md`. It currently ends at `## 10. Public (unauthenticated) routes`
(around line 115) — there's no existing note about `verify-slice`'s isolated output directory. Append a
new section after it, matching the existing numbering style:

```markdown
## 11. Isolated slice directories

Three separate slice output directories exist under the data dir, and none may ever write into
another's: `gcode/<job_id>` (production — a live upload/print may be reading from this), `gcode_verify/
<job_id>` (`verify-slice`, debug-only), `gcode_manual_complete/<job_id>` (`complete-manually`). Both
non-production paths exist specifically so a debug/manual slice action can safely run even while a job
is genuinely `printing`/`uploading` through the production path. If you add another slice-invoking
route, give it its own isolated subdirectory rather than reusing one of these.
```

- [ ] **Step 3: Commit**

```bash
git add docs/agent/data-model.md docs/agent/backend-review.md
git commit -m "Document complete-manually and the isolated-slice-directory convention"
```

---

### Task 8: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite**

Run: `cd backend && pytest -v`
Expected: PASS, no failures

- [ ] **Step 2: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: PASS, no failures

- [ ] **Step 3: Run the frontend production build**

Run: `cd frontend && npm run build`
Expected: success, no TypeScript errors

- [ ] **Step 4: Manually smoke-test the golden path**

With the dev servers running (`themis-start` skill, or `uvicorn app.main:app --reload --port 8001` from
`backend/` and `npm run dev` from `frontend/`):
1. Create a job with a real 3MF against a printer that has a configured OrcaSlicer machine preset.
2. Open the job's detail page, click "Mark as already completed".
3. Confirm the warning shows and the button says "Confirm" (not an immediate action).
4. Click Confirm — observe a brief "Slicing…" state, then the job shows `complete` with real
   `actual_filament_grams`/`actual_seconds` values (compare against Files → the same file's normal
   slice estimate, to sanity-check they're in the same ballpark).
5. Check Fleet — the printer should show as awaiting plate clear.
6. If Spoolman is configured with a loaded spool on that printer, confirm the spool's usage increased
   by the expected grams.
7. Confirm no webhook/notification fired (if either is configured for `job.complete`, watch the
   configured channel — nothing should arrive).

This step has no automated check; note the result in your final report to the user.

- [ ] **Step 5: No commit for this task** — it's verification only, nothing to add to git.

---

## Post-implementation

Per this project's CLAUDE.md "Development workflow": after all tasks pass, dispatch exactly one fresh,
non-fork reviewer subagent (base = `develop`, head = current branch tip), pointing it at
`docs/agent/backend-review.md` and `docs/agent/frontend-review.md`. Pay particular attention to:
- That `complete-manually` never calls anything on the printer's vendor client (no `start_print`,
  `upload_file`, `stop_print`, etc.) — grep the diff for any such call.
- That the three isolated slice directories (`gcode`, `gcode_verify`, `gcode_manual_complete`) truly
  never collide — re-verify by reading the actual path construction, not just the tests.
- That no webhook/notification call was accidentally introduced on either the success or failure path.
- Byte-for-byte field-name agreement between the job fields the frontend reads
  (`actual_filament_grams`, `actual_seconds`, `assigned_printer_id`, `printer_configs[].printer_id`)
  and what `_to_dict`/`ApiJobDetails` actually carry.

Address any Critical/Important findings, then write `.claude/review-state.json` before opening a PR
into `develop`, per the repo's PR-review gate.
