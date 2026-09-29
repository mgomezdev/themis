"""The shared factories in conftest.py must themselves be trustworthy: other tests build on them."""
from app.models import Job, Printer


async def test_upload_3mf_returns_id_of_a_listed_file(client, upload_3mf):
    file_id = await upload_3mf("part.3mf")
    files = (await client.get("/api/v1/files")).json()
    assert [f["id"] for f in files] == [file_id]


async def test_create_printer_applies_overrides(client, create_printer):
    printer_id = await create_printer(name="Forge")
    printer = (await client.get(f"/api/v1/printers/{printer_id}")).json()
    assert printer["name"] == "Forge"
    assert printer["current_orca_printer_profile"] == "Bambu Lab P1S 0.4"


async def test_create_job_uploads_and_creates_printer_when_not_given(client, create_job):
    job_id = await create_job(filament_profile="PLA")
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["status"] == "queued"
    assert job["plate_number"] == 1


async def test_session_factory_sees_the_same_db_as_the_client(session_factory, create_job, create_printer):
    printer_id = await create_printer()
    job_id = await create_job(printer_id=printer_id)
    async with session_factory() as s:
        assert (await s.get(Printer, printer_id)).name == "P1S"
        assert (await s.get(Job, job_id)).status == "queued"


async def test_create_job_links_the_order_when_given(client, create_job):
    resp = await client.post("/api/v1/orders", json={
        "order_type": "customer", "customer": "Vela Robotics", "title": "Brackets", "due_date": "2026-06-01",
        "notes": "", "parts": [{"name": "Arm L", "qty": 8, "material": "PA-CF", "est_minutes": 78}],
    })
    assert resp.status_code == 201, resp.text
    order = resp.json()

    job_id = await create_job(order_id=order["id"])
    unlinked_id = await create_job()

    assert (await client.get(f"/api/v1/jobs/{job_id}")).json()["order_id"] == order["id"]
    assert (await client.get(f"/api/v1/jobs/{unlinked_id}")).json()["order_id"] is None


def test_make_3mf_bytes_is_identical_whatever_the_clock_says():
    """Same bytes => same content hash => the upload route deduplicates instead of suffixing "(2)"."""
    import time
    from unittest.mock import patch
    from tests.conftest import make_3mf_bytes

    with patch("zipfile.time.localtime", return_value=time.struct_time((2030, 5, 5, 5, 5, 5, 0, 0, 0))):
        later = make_3mf_bytes()
    with patch("zipfile.time.localtime", return_value=time.struct_time((2031, 6, 6, 6, 6, 6, 0, 0, 0))):
        much_later = make_3mf_bytes()

    assert later == much_later == make_3mf_bytes()


async def test_uploading_the_same_factory_3mf_twice_reuses_one_file(client, upload_3mf):
    first = await upload_3mf()
    second = await upload_3mf()

    assert first == second
    assert [f["original_filename"] for f in (await client.get("/api/v1/files")).json()] == ["m.3mf"]

