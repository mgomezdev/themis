# backend/tests/api/test_queue_api.py
import pytest
from unittest.mock import AsyncMock, patch
from app.services.providers.filament_inventory import Spool
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import spool, use_provider


async def test_queue_empty(client):
    response = await client.get("/api/v1/queue")
    assert response.status_code == 200
    assert response.json() == []


async def test_queue_shows_active_jobs(client, create_job):
    job_id = await create_job(filament_profile="PLA")
    response = await client.get("/api/v1/queue")
    assert response.status_code == 200
    ids = [j["id"] for j in response.json()]
    assert job_id in ids


async def test_queue_shows_sliced_jobs(client, session_factory, create_job):
    """A parked "sliced" job (production gcode ready, printer not yet ready to
    receive) must appear in GET /api/v1/queue with its full enriched fields —
    otherwise the frontend only learns about it via the queue_update websocket
    broadcast (which sends only id/status/queue_position), synthesizing a
    mostly-empty job entry ("Plate undefined")."""
    from app.models import Job

    job_id = await create_job(filament_profile="PLA")
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        job.status = "sliced"
        await session.commit()

    response = await client.get("/api/v1/queue")
    assert response.status_code == 200
    ids = [j["id"] for j in response.json()]
    assert job_id in ids


async def test_queue_reorder(client, create_job):
    job1 = await create_job(filament_profile="PLA")
    job2 = await create_job(filament_profile="PLA")
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


async def _seed_queue_spool_warning_fixture(session_factory, estimate_grams, spool_id="99", position=1.0):
    """Seed UploadedFile/Printer/Job/JobPrinterConfig rows directly via the test
    session, wired so the job's JobPrinterConfig resolves (via _slot_for_config)
    to the printer's loaded_filaments[0] slot, which carries a spoolman_spool_id.
    Returns (job_id, printer_id)."""
    from app.models import UploadedFile, Job, JobPrinterConfig, Printer

    async with session_factory() as session:
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
        await session.commit()
        job_id, printer_id = j.id, p.id
    return job_id, printer_id


async def test_queue_low_stock_warning_none_when_sufficient(client, session_factory):
    """GET /api/v1/queue: low_stock_warning is None when the bound spool has
    enough filament remaining for the job's estimated grams."""
    job_id, _printer_id = await _seed_queue_spool_warning_fixture(session_factory, estimate_grams=200.0)

    fake = FakeInventoryProvider(spools=[spool("99", 900.0, name="Bambu PLA Basic Black", material="PLA")])
    await use_provider(fake)
    resp = await client.get("/api/v1/queue")
    assert resp.status_code == 200
    job = next(j for j in resp.json() if j["id"] == job_id)
    assert job["low_stock_warning"] is None


async def test_queue_low_stock_warning_set_when_insufficient(client, session_factory):
    """GET /api/v1/queue: low_stock_warning is populated, with both needed and
    remaining grams in the message, when the bound spool is short on filament."""
    job_id, _printer_id = await _seed_queue_spool_warning_fixture(session_factory, estimate_grams=340.0)

    fake = FakeInventoryProvider(spools=[spool("99", 220.0, name="Bambu PLA Basic Black", material="PLA")])
    await use_provider(fake)
    resp = await client.get("/api/v1/queue")
    assert resp.status_code == 200
    job = next(j for j in resp.json() if j["id"] == job_id)
    warning = job["low_stock_warning"]
    assert warning is not None
    assert "340" in warning["message"]
    assert "220" in warning["message"]


async def test_queue_fetch_spools_called_once_for_multiple_jobs(client, session_factory):
    """GET /api/v1/queue must batch: even with 2+ active jobs each needing a
    spool lookup, fetch_spools is called exactly once per request, not once
    per job/config (would be an N+1 problem on a frequently-polled endpoint)."""
    job1, _ = await _seed_queue_spool_warning_fixture(session_factory, estimate_grams=340.0, spool_id="99", position=1.0)
    job2, _ = await _seed_queue_spool_warning_fixture(session_factory, estimate_grams=340.0, spool_id="99", position=2.0)

    fake = FakeInventoryProvider(spools=[spool("99", 220.0, name="Bambu PLA Basic Black", material="PLA")])
    await use_provider(fake)
    resp = await client.get("/api/v1/queue")
    assert resp.status_code == 200
    ids = [j["id"] for j in resp.json()]
    assert job1 in ids
    assert job2 in ids
    assert fake.calls.count("list_spools") == 1
