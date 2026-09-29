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
