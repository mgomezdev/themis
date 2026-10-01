"""not_before on jobs (create + PATCH /schedule) and quiet hours on printers."""
from unittest.mock import patch

import pytest

from app.models import Job

FUTURE = "2030-01-01T22:00:00+00:00"


async def _create(client, upload_3mf, create_printer, **extra):
    body = {"uploaded_file_id": await upload_3mf(), "plate_number": 1,
            "printer_configs": [{"printer_id": await create_printer(), "print_profile": "0.20mm",
                                 "filament_type": "any", "filament_color": "any"}], **extra}
    with patch("app.api.routes.jobs.queue_engine"):
        return await client.post("/api/v1/jobs", json=body)


async def test_a_job_can_be_created_with_a_start_time_and_it_is_normalised_to_utc(client, upload_3mf, create_printer):
    r = await _create(client, upload_3mf, create_printer, not_before="2030-01-01T23:00:00+01:00")
    assert r.status_code == 201, r.text
    assert r.json()["not_before"] == FUTURE
    assert (await client.get(f"/api/v1/jobs/{r.json()['id']}")).json()["not_before"] == FUTURE
    assert [j["not_before"] for j in (await client.get("/api/v1/jobs")).json()] == [FUTURE]


async def test_a_job_without_a_start_time_has_none(client, upload_3mf, create_printer):
    r = await _create(client, upload_3mf, create_printer)
    assert r.json()["not_before"] is None


async def test_create_rejects_a_malformed_start_time(client, upload_3mf, create_printer):
    r = await _create(client, upload_3mf, create_printer, not_before="tomorrow-ish")
    assert r.status_code == 422


async def test_schedule_patch_sets_changes_and_clears_the_start_time_and_wakes_the_engine(client, upload_3mf, create_printer):
    job_id = (await _create(client, upload_3mf, create_printer)).json()["id"]

    with patch("app.api.routes.jobs.queue_engine") as engine:
        r = await client.patch(f"/api/v1/jobs/{job_id}/schedule", json={"not_before": FUTURE})
        assert (r.status_code, r.json()["not_before"]) == (200, FUTURE)
        r = await client.patch(f"/api/v1/jobs/{job_id}/schedule", json={"not_before": None})
        assert r.json()["not_before"] is None
    assert engine.wake.call_count == 2                                         # clearing releases the job immediately
    assert (await client.get(f"/api/v1/jobs/{job_id}")).json()["not_before"] is None


async def test_schedule_patch_refuses_a_job_that_already_started_and_a_bad_time(client, session_factory, upload_3mf, create_printer):
    job_id = (await _create(client, upload_3mf, create_printer)).json()["id"]
    assert (await client.patch(f"/api/v1/jobs/{job_id}/schedule", json={"not_before": "nope"})).status_code == 422
    assert (await client.patch("/api/v1/jobs/9999/schedule", json={"not_before": FUTURE})).status_code == 404

    async with session_factory() as s:
        (await s.get(Job, job_id)).status = "printing"
        await s.commit()
    r = await client.patch(f"/api/v1/jobs/{job_id}/schedule", json={"not_before": FUTURE})
    assert r.status_code == 409
    async with session_factory() as s:
        assert (await s.get(Job, job_id)).not_before is None                    # unchanged


async def test_printer_quiet_hours_round_trip_and_can_be_cleared(client, create_printer):
    pid = await create_printer()
    assert (await client.get(f"/api/v1/printers/{pid}")).json()["quiet_start"] is None

    r = await client.patch(f"/api/v1/printers/{pid}", json={"quiet_start": "22:00", "quiet_end": "06:00"})
    assert r.status_code == 200, r.text
    got = (await client.get(f"/api/v1/printers/{pid}")).json()
    assert (got["quiet_start"], got["quiet_end"]) == ("22:00", "06:00")
    fleet = next(p for p in (await client.get("/api/v1/fleet")).json() if p["id"] == pid)
    assert (fleet["quiet_start"], fleet["quiet_end"]) == ("22:00", "06:00")

    await client.patch(f"/api/v1/printers/{pid}", json={"name": "Renamed"})       # unrelated edit keeps the window
    assert (await client.get(f"/api/v1/printers/{pid}")).json()["quiet_start"] == "22:00"

    await client.patch(f"/api/v1/printers/{pid}", json={"quiet_start": None, "quiet_end": None})
    assert (await client.get(f"/api/v1/printers/{pid}")).json()["quiet_start"] is None


@pytest.mark.parametrize("body", [
    {"quiet_start": "22:00"},                         # half a window
    {"quiet_end": "06:00"},
    {"quiet_start": "25:00", "quiet_end": "06:00"},   # not a time
    {"quiet_start": "9:00", "quiet_end": "17:00"},    # not zero-padded
])
async def test_quiet_hours_validation(client, create_printer, body):
    pid = await create_printer()
    assert (await client.patch(f"/api/v1/printers/{pid}", json=body)).status_code == 422
    assert (await client.get(f"/api/v1/printers/{pid}")).json()["quiet_start"] is None
