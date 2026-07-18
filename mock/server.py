"""Themis mock server for testing and third-party integration.

Provides an in-memory implementation of the Themis REST API — no database,
no printer connections, no slicer required. Safe to use in any CI pipeline.

Run standalone:
    uvicorn server:app --host 0.0.0.0 --port 8000
Or via Docker:
    docker run -p 8000:8000 ninjabuffalo/themis-mock:1
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

app = FastAPI(
    title="Themis",
    description="Mock Themis print-queue API — in-memory, no hardware required.",
    version="mock",
)

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

_next_id: dict[str, int] = {"printer": 1, "file": 1, "job": 1, "project": 1, "order": 1, "tag": 1}

def _id(kind: str) -> int:
    v = _next_id[kind]
    _next_id[kind] += 1
    return v

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

_printers: dict[int, dict] = {}
_files: dict[int, dict] = {}
_jobs: dict[int, dict] = {}
_projects: dict[int, dict] = {}
_orders: dict[int, dict] = {}
_tags: dict[int, dict] = {}

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/v1/health")
async def health():
    return {"status": "ok"}

# ---------------------------------------------------------------------------
# Printers
# ---------------------------------------------------------------------------

MOCK_PRINTER_TYPES = [
    {"type": "mock", "display_name": "Mock Printer (Testing)", "fields": []},
    {"type": "bambu", "display_name": "Bambu Lab", "fields": [
        {"name": "ip", "label": "IP Address", "type": "text", "required": True},
        {"name": "access_code", "label": "Access Code", "type": "password", "required": True},
        {"name": "serial", "label": "Serial Number", "type": "text", "required": True},
    ]},
]

def _printer_shape(p: dict) -> dict:
    return {
        "id": p["id"],
        "name": p["name"],
        "printer_type": p["printer_type"],
        "connection_config": p.get("connection_config", {}),
        "awaiting_plate_clear": p.get("awaiting_plate_clear", False),
        "orca_printer_profiles": p.get("orca_printer_profiles", []),
        "current_orca_printer_profile": p.get("current_orca_printer_profile"),
        "enabled": p.get("enabled", True),
        "queue_on": p.get("queue_on", True),
        "loaded_filaments": p.get("loaded_filaments", []),
        "build_plate_type": p.get("build_plate_type"),
        "no_snapshots_while_idle": p.get("no_snapshots_while_idle", False),
        "bed_x_mm": p.get("bed_x_mm", 256.0),
        "bed_y_mm": p.get("bed_y_mm", 256.0),
        "connected": True,
    }


@app.get("/api/v1/printers/types")
async def list_printer_types() -> list[dict]:
    return MOCK_PRINTER_TYPES


@app.get("/api/v1/printers")
async def list_printers() -> list[dict]:
    return [_printer_shape(p) for p in _printers.values()]


@app.post("/api/v1/printers", status_code=201)
async def create_printer(body: dict) -> dict:
    printer = {**body, "id": _id("printer"), "awaiting_plate_clear": False, "enabled": True, "queue_on": True}
    _printers[printer["id"]] = printer
    return _printer_shape(printer)


@app.get("/api/v1/printers/{printer_id}")
async def get_printer(printer_id: int) -> dict:
    p = _printers.get(printer_id)
    if p is None:
        raise HTTPException(404, f"Printer {printer_id} not found")
    return _printer_shape(p)


@app.patch("/api/v1/printers/{printer_id}")
async def update_printer(printer_id: int, body: dict) -> dict:
    p = _printers.get(printer_id)
    if p is None:
        raise HTTPException(404, f"Printer {printer_id} not found")
    p.update({k: v for k, v in body.items() if v is not None})
    return _printer_shape(p)


@app.delete("/api/v1/printers/{printer_id}")
async def delete_printer(printer_id: int) -> dict:
    if printer_id not in _printers:
        raise HTTPException(404, f"Printer {printer_id} not found")
    del _printers[printer_id]
    return {"deleted": printer_id}


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def _file_shape(f: dict) -> dict:
    return {
        "id": f["id"],
        "original_filename": f["original_filename"],
        "relative_path": f.get("relative_path", f["original_filename"]),
        "folder": f.get("folder", "Job Uploads"),
        "size_bytes": f.get("size_bytes", 0),
        "plate_count": f.get("plate_count", 1),
        "uploaded_at": f.get("uploaded_at", _now()),
        "missing": False,
        "tags": f.get("tags", []),
        "thumbnail_url": None,
        "plate_thumbnails": [],
    }


@app.get("/api/v1/files")
async def list_files() -> list[dict]:
    return [_file_shape(f) for f in _files.values()]


@app.post("/api/v1/files", status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    folder: Optional[str] = Form(None),
) -> dict:
    content = await file.read()
    record = {
        "id": _id("file"),
        "original_filename": file.filename or "upload.gcode",
        "folder": folder or "Job Uploads",
        "size_bytes": len(content),
        "plate_count": 1,
        "uploaded_at": _now(),
    }
    _files[record["id"]] = record
    return _file_shape(record)


@app.get("/api/v1/files/{file_id}")
async def get_file(file_id: int) -> dict:
    f = _files.get(file_id)
    if f is None:
        raise HTTPException(404, f"File {file_id} not found")
    return _file_shape(f)


@app.delete("/api/v1/files/{file_id}")
async def delete_file(file_id: int) -> dict:
    if file_id not in _files:
        raise HTTPException(404, f"File {file_id} not found")
    del _files[file_id]
    return {"deleted": file_id}


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

_CANCELLABLE = {"queued", "blocked", "slicing", "sliced", "uploading", "printing", "paused", "failed"}

def _job_shape(j: dict) -> dict:
    return {
        "id": j["id"],
        "uploaded_file_id": j.get("uploaded_file_id"),
        "plate_number": j.get("plate_number", 1),
        "order_id": j.get("order_id"),
        "project_id": j.get("project_id"),
        "assigned_printer_id": j.get("assigned_printer_id"),
        "queue_position": j.get("queue_position", 0.0),
        "status": j.get("status", "queued"),
        "overrides": j.get("overrides"),
        "outcome": j.get("outcome"),
        "project_item_quantities": j.get("project_item_quantities"),
        "created_at": j.get("created_at", _now()),
        "updated_at": j.get("updated_at"),
        "completed_at": j.get("completed_at"),
        "actual_filament_grams": j.get("actual_filament_grams"),
        "actual_seconds": j.get("actual_seconds"),
        "actual_filament_breakdown": j.get("actual_filament_breakdown"),
        "deduction_skipped": j.get("deduction_skipped", False),
        "estimate_status": j.get("estimate_status"),
        "estimate_seconds": j.get("estimate_seconds"),
        "estimate_filament_grams": j.get("estimate_filament_grams"),
        "estimate_filament_breakdown": j.get("estimate_filament_breakdown"),
        "estimate_preset_label": j.get("estimate_preset_label"),
    }


@app.get("/api/v1/jobs")
async def list_jobs() -> list[dict]:
    active = {"queued", "blocked", "slicing", "sliced", "uploading", "printing", "paused", "failed"}
    return [_job_shape(j) for j in _jobs.values() if j.get("status") in active]


@app.get("/api/v1/jobs/history")
async def list_job_history() -> list[dict]:
    terminal = {"complete", "cancelled"}
    return [_job_shape(j) for j in _jobs.values() if j.get("status") in terminal]


@app.post("/api/v1/jobs", status_code=201)
async def create_job(body: dict) -> dict:
    if not body.get("printer_configs"):
        raise HTTPException(422, "printer_configs must not be empty")
    file_id = body.get("uploaded_file_id")
    if file_id and file_id not in _files:
        raise HTTPException(404, f"File {file_id} not found")
    q_pos = float(len(_jobs) + 1)
    job = {
        **body,
        "id": _id("job"),
        "status": "queued",
        "assigned_printer_id": None,
        "queue_position": q_pos,
        "created_at": _now(),
    }
    _jobs[job["id"]] = job
    return _job_shape(job)


@app.get("/api/v1/jobs/{job_id}")
async def get_job(job_id: int) -> dict:
    j = _jobs.get(job_id)
    if j is None:
        raise HTTPException(404, f"Job {job_id} not found")
    return _job_shape(j)


@app.post("/api/v1/jobs/{job_id}/cancel")
async def cancel_job(job_id: int) -> dict:
    j = _jobs.get(job_id)
    if j is None:
        raise HTTPException(404, f"Job {job_id} not found")
    if j["status"] not in _CANCELLABLE:
        raise HTTPException(409, f"Job {job_id} cannot be cancelled in status '{j['status']}'")
    j["status"] = "cancelled"
    j["updated_at"] = _now()
    return _job_shape(j)


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

@app.get("/api/v1/queue")
async def get_queue() -> list[dict]:
    queued = {"queued", "blocked"}
    ordered = sorted(
        [j for j in _jobs.values() if j.get("status") in queued],
        key=lambda j: j.get("queue_position", 0),
    )
    return [_job_shape(j) for j in ordered]


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def _project_shape(p: dict) -> dict:
    return {
        "id": p["id"],
        "name": p["name"],
        "created_at": p.get("created_at", _now()),
        "item_count": 0,
    }


@app.get("/api/v1/projects")
async def list_projects() -> list[dict]:
    return [_project_shape(p) for p in _projects.values()]


@app.post("/api/v1/projects", status_code=201)
async def create_project(body: dict) -> dict:
    proj = {**body, "id": _id("project"), "created_at": _now()}
    _projects[proj["id"]] = proj
    return _project_shape(proj)


@app.get("/api/v1/projects/{project_id}")
async def get_project(project_id: int) -> dict:
    p = _projects.get(project_id)
    if p is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return _project_shape(p)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

def _order_shape(o: dict) -> dict:
    return {
        "id": o["id"],
        "name": o.get("name", ""),
        "status": o.get("status", "open"),
        "created_at": o.get("created_at", _now()),
    }


@app.get("/api/v1/orders")
async def list_orders() -> list[dict]:
    return [_order_shape(o) for o in _orders.values()]


@app.post("/api/v1/orders", status_code=201)
async def create_order(body: dict) -> dict:
    order = {**body, "id": _id("order"), "created_at": _now(), "status": "open"}
    _orders[order["id"]] = order
    return _order_shape(order)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

@app.get("/api/v1/tags")
async def list_tags() -> list[dict]:
    return list(_tags.values())


@app.post("/api/v1/tags", status_code=201)
async def create_tag(body: dict) -> dict:
    tag = {**body, "id": _id("tag")}
    _tags[tag["id"]] = tag
    return tag


@app.delete("/api/v1/tags/{tag_id}")
async def delete_tag(tag_id: int) -> dict:
    if tag_id not in _tags:
        raise HTTPException(404, f"Tag {tag_id} not found")
    del _tags[tag_id]
    return {"deleted": tag_id}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_settings: dict = {}

@app.get("/api/v1/settings")
async def get_settings() -> dict:
    return _settings


@app.patch("/api/v1/settings")
async def update_settings(body: dict) -> dict:
    _settings.update(body)
    return _settings
