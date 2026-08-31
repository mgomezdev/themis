# backend/tests/api/test_queue_api.py
import json
import io
import zipfile
import pytest
from unittest.mock import patch


def _make_3mf() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/slice_info.config", json.dumps({
            "plate": [{"index": 1, "prediction": 60, "weight": [5.0]}]
        }))
        zf.writestr("Metadata/plate_1.png", b"\x89PNG")
    return buf.getvalue()


async def _create_job(client, tmp_path) -> int:
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"):
        (tmp_path / "library").mkdir(exist_ok=True)
        (tmp_path / "filecache").mkdir(exist_ok=True)
        upload = await client.post(
            "/api/v1/files/upload",
            files={"file": ("m.3mf", _make_3mf(), "application/octet-stream")},
        )
    file_id = upload.json()["id"]
    printer = await client.post("/api/v1/printers", json={
        "name": "P", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = printer.json()["id"]
    with patch("app.api.routes.jobs.queue_engine"):
        create = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id,
            "plate_number": 1,
            "printer_configs": [
                {"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA", "filament_type": "any", "filament_color": "any"}
            ],
        })
    return create.json()["id"]


async def test_queue_empty(client):
    response = await client.get("/api/v1/queue")
    assert response.status_code == 200
    assert response.json() == []


async def test_queue_shows_active_jobs(client, tmp_path):
    job_id = await _create_job(client, tmp_path)
    response = await client.get("/api/v1/queue")
    assert response.status_code == 200
    ids = [j["id"] for j in response.json()]
    assert job_id in ids


async def test_queue_shows_sliced_jobs(client, tmp_path):
    """A parked "sliced" job (production gcode ready, printer not yet ready to
    receive) must appear in GET /api/v1/queue with its full enriched fields —
    otherwise the frontend only learns about it via the queue_update websocket
    broadcast (which sends only id/status/queue_position), synthesizing a
    mostly-empty job entry ("Plate undefined")."""
    from app.models import Job
    from app.main import app
    from app.database import get_session

    job_id = await _create_job(client, tmp_path)
    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    job = await session.get(Job, job_id)
    job.status = "sliced"
    await session.commit()
    await agen.aclose()

    response = await client.get("/api/v1/queue")
    assert response.status_code == 200
    ids = [j["id"] for j in response.json()]
    assert job_id in ids


async def test_queue_reorder(client, tmp_path):
    job1 = await _create_job(client, tmp_path)
    job2 = await _create_job(client, tmp_path)
    response = await client.patch("/api/v1/queue/reorder", json={
        "positions": [{"job_id": job1, "queue_position": 5.0}, {"job_id": job2, "queue_position": 3.0}]
    })
    assert response.status_code == 200
    queue = await client.get("/api/v1/queue")
    ordered_ids = [j["id"] for j in queue.json()]
    assert ordered_ids.index(job2) < ordered_ids.index(job1)


async def test_queue_reorder_unknown_job(client):
    response = await client.patch("/api/v1/queue/reorder", json={
        "positions": [{"job_id": 9999, "queue_position": 1.0}]
    })
    assert response.status_code == 404


async def _seed_queue_spool_warning_fixture(estimate_grams, spool_id="99", position=1.0):
    """Seed UploadedFile/Printer/Job/JobPrinterConfig rows directly via the test
    session, wired so the job's JobPrinterConfig resolves (via _slot_for_config)
    to the printer's loaded_filaments[0] slot, which carries a spoolman_spool_id.
    Returns (job_id, printer_id)."""
    from app.models import UploadedFile, Job, JobPrinterConfig, Printer, SpoolmanConfig
    from app.main import app
    from app.database import get_session

    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    f = UploadedFile(original_filename="x.3mf", stored_path="/t/x.3mf",
                      plates=[], uploaded_at="2026-01-01T00:00:00")
    p = Printer(name="P1S", printer_type="bambu", connection_config={},
                loaded_filaments=[{"slot": 0, "type": "PLA", "color": "", "spoolman_spool_id": spool_id}])
    session.add_all([f, p])
    await session.flush()
    j = Job(uploaded_file_id=f.id, plate_number=1, status="queued",
            queue_position=position, created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00", estimate_filament_grams=estimate_grams)
    session.add(j)
    await session.flush()
    cfg = JobPrinterConfig(job_id=j.id, printer_id=p.id, print_profile="0.20mm",
                            filament_type="any", filament_color="any")
    session.add(cfg)
    spoolman_cfg = await session.get(SpoolmanConfig, 1)
    if spoolman_cfg is None:
        spoolman_cfg = SpoolmanConfig(id=1, enabled=True, url="http://spoolman.local", api_key=None)
        session.add(spoolman_cfg)
    else:
        spoolman_cfg.enabled = True
        spoolman_cfg.url = "http://spoolman.local"
    await session.commit()
    job_id, printer_id = j.id, p.id
    await agen.aclose()
    return job_id, printer_id


async def test_queue_low_stock_warning_none_when_sufficient(client):
    """GET /api/v1/queue: low_stock_warning is None when the bound spool has
    enough filament remaining for the job's estimated grams."""
    job_id, _printer_id = await _seed_queue_spool_warning_fixture(estimate_grams=200.0)

    fake_spool = {"id": 99, "remaining_weight": 900.0,
                  "filament": {"name": "Bambu PLA Basic Black", "material": "PLA"}}
    with patch("app.api.routes.queue.fetch_spools", return_value=[fake_spool]):
        resp = await client.get("/api/v1/queue")
    assert resp.status_code == 200
    job = next(j for j in resp.json() if j["id"] == job_id)
    assert job["low_stock_warning"] is None


async def test_queue_low_stock_warning_set_when_insufficient(client):
    """GET /api/v1/queue: low_stock_warning is populated, with both needed and
    remaining grams in the message, when the bound spool is short on filament."""
    job_id, _printer_id = await _seed_queue_spool_warning_fixture(estimate_grams=340.0)

    fake_spool = {"id": 99, "remaining_weight": 220.0,
                  "filament": {"name": "Bambu PLA Basic Black", "material": "PLA"}}
    with patch("app.api.routes.queue.fetch_spools", return_value=[fake_spool]):
        resp = await client.get("/api/v1/queue")
    assert resp.status_code == 200
    job = next(j for j in resp.json() if j["id"] == job_id)
    warning = job["low_stock_warning"]
    assert warning is not None
    assert "340" in warning["message"]
    assert "220" in warning["message"]


async def test_queue_fetch_spools_called_once_for_multiple_jobs(client):
    """GET /api/v1/queue must batch: even with 2+ active jobs each needing a
    spool lookup, fetch_spools is called exactly once per request, not once
    per job/config (would be an N+1 problem on a frequently-polled endpoint)."""
    job1, _ = await _seed_queue_spool_warning_fixture(estimate_grams=340.0, spool_id="99", position=1.0)
    job2, _ = await _seed_queue_spool_warning_fixture(estimate_grams=340.0, spool_id="99", position=2.0)

    fake_spool = {"id": 99, "remaining_weight": 220.0,
                  "filament": {"name": "Bambu PLA Basic Black", "material": "PLA"}}
    with patch("app.api.routes.queue.fetch_spools", return_value=[fake_spool]) as mock_fetch:
        resp = await client.get("/api/v1/queue")
    assert resp.status_code == 200
    ids = [j["id"] for j in resp.json()]
    assert job1 in ids
    assert job2 in ids
    mock_fetch.assert_called_once()
