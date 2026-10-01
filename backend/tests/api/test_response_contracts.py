"""The response keys the frontend reads (contracts/response-keys.json) exist in the backend's real responses.

The same file is checked against the frontend's TypeScript interfaces by
frontend/src/api/responseKeys.contract.test.ts, so a renamed or dropped field fails one side or the other.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.abstract_printer_client import PrinterCapabilities
from app.services.printer_manager import printer_manager

CONTRACT = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "response-keys.json").read_text())


def assert_carries(name: str, payload: dict) -> None:
    missing = sorted(set(CONTRACT[name]) - set(payload))
    assert not missing, f"{name}: response is missing keys the frontend reads: {missing} (has {sorted(payload)})"


async def test_printer_list_and_detail_carry_the_printer_keys(client, create_printer):
    printer_id = await create_printer()

    assert_carries("printer", (await client.get("/api/v1/printers")).json()[0])
    assert_carries("printer", (await client.get(f"/api/v1/printers/{printer_id}")).json())


async def test_fleet_items_carry_the_fleet_keys_offline_and_the_fan_keys_when_connected(client, create_printer):
    offline_id = await create_printer(name="Cold")
    live_id = await create_printer(name="Live", printer_type="elegoo_centauri", connection_config={"ip_address": "10.0.0.1"})
    live = MagicMock(printer_type="elegoo_centauri", connected=True)
    live.state = MagicMock(connected=True, state="RUNNING", progress=10.0, total_ticks=0, current_ticks=0, remaining_time=5,
                           filename="a.3mf", temperatures={"nozzle": 200.0}, layer_num=1, total_layers=2,
                           fan_model=1, fan_aux=2, fan_box=3, print_speed_pct=100)
    live.get_capabilities.return_value = PrinterCapabilities()
    printer_manager._clients[live_id] = live

    items = {i["id"]: i for i in (await client.get("/api/v1/fleet")).json()}

    assert_carries("fleet_printer", items[offline_id])
    assert_carries("fleet_printer", items[live_id])
    assert_carries("fleet_printer_connected_only", items[live_id])  # offline items legitimately lack these (the UI defaults them to 0)


async def test_queue_items_carry_the_job_keys(client, create_job):
    await create_job()

    (job,) = (await client.get("/api/v1/queue")).json()

    assert_carries("queue_job", job)


async def test_job_details_carry_the_job_and_detail_keys(client, create_job):
    job_id = await create_job()

    details = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()

    assert_carries("job_details_core", details)
    assert_carries("job_details_extra", details)


async def test_project_detail_items_links_parts_and_jobs_carry_their_keys(client, upload_3mf, create_job, session_factory):
    from app.models import Job

    project_id = (await client.post("/api/v1/projects", json={"name": "P"})).json()["id"]
    file_id = await upload_3mf()
    await client.post(f"/api/v1/projects/{project_id}/items", json={"file_id": file_id, "quantity": 2})
    await client.post(f"/api/v1/projects/{project_id}/links", json={"url": "https://x.example", "label": "L"})
    await client.post(f"/api/v1/projects/{project_id}/parts", json={"name": "screw", "quantity": 4})
    job_id = await create_job(file_id=file_id)
    async with session_factory() as s:
        (await s.get(Job, job_id)).project_id = project_id
        await s.commit()

    project = (await client.get(f"/api/v1/projects/{project_id}")).json()
    project_jobs = (await client.get(f"/api/v1/projects/{project_id}/jobs")).json()

    assert_carries("project", project)
    assert_carries("project_item", project["items"][0])
    assert_carries("project_link", project["links"][0])
    assert_carries("project_part", project["parts"][0])
    assert_carries("project_job", project_jobs[0])
    assert_carries("project", (await client.get("/api/v1/projects")).json()[0])


async def test_library_files_carry_the_file_keys(client, upload_3mf):
    await upload_3mf()

    (row,) = (await client.get("/api/v1/files")).json()

    assert_carries("library_file", row)


async def test_fleet_analytics_carries_the_analytics_keys(client, create_printer, upload_3mf, session_factory):
    from datetime import datetime, timezone

    from app.models import Job

    printer_id = await create_printer()
    file_id = await upload_3mf()
    now = datetime.now(timezone.utc).isoformat()
    async with session_factory() as s:
        s.add(Job(uploaded_file_id=file_id, status="complete", assigned_printer_id=printer_id, completed_at=now,
                  actual_seconds=60, actual_filament_grams=1.0, actual_filament_breakdown=[{"filament_profile": "PLA", "grams": 1.0}],
                  created_at=now, updated_at=now))
        await s.commit()

    body = (await client.get("/api/v1/fleet/analytics")).json()

    assert_carries("analytics", body)
    assert_carries("analytics_range", body["range"])
    assert_carries("analytics_totals", body["totals"])
    assert_carries("analytics_printer", body["printers"][0])
    assert_carries("analytics_material", body["materials"][0])
async def test_payments_carry_the_payment_keys(client):
    customer = (await client.post("/api/v1/customers", json={"name": "A", "email": "a@x.test"})).json()
    project = (await client.post("/api/v1/projects", json={"name": "P", "price": 10, "customer_id": customer["id"]})).json()
    await client.post(f"/api/v1/projects/{project['id']}/payments", json={"amount": 4, "note": "deposit"})

    (own,) = (await client.get(f"/api/v1/projects/{project['id']}/payments")).json()
    (via_customer,) = (await client.get(f"/api/v1/customers/{customer['id']}/payments")).json()

    assert_carries("project_payment", own)
    assert_carries("customer_payment", via_customer)


def test_the_contract_helper_reports_missing_keys():
    with pytest.raises(AssertionError, match="missing keys the frontend reads: \\['name'\\]"):
        assert_carries("project_part", {k: 1 for k in CONTRACT["project_part"] if k != "name"})
