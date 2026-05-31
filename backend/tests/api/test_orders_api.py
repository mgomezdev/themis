# backend/tests/api/test_orders_api.py
import io
import json
import zipfile
from unittest.mock import patch


def _make_3mf() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/slice_info.config", json.dumps({
            "plate": [{"index": 1, "prediction": 60, "weight": [5.0]}]
        }))
        zf.writestr("Metadata/plate_1.png", b"\x89PNG")
    return buf.getvalue()


async def _create_order(client, **over):
    body = {
        "order_type": "customer", "customer": "Vela Robotics",
        "title": "Brackets", "due_date": "2026-06-01", "notes": "match black",
        "parts": [{"name": "Arm L", "qty": 8, "material": "PA-CF", "est_minutes": 78}],
    }
    body.update(over)
    return await client.post("/api/v1/orders", json=body)


async def _make_job(client, tmp_path, order_id, status="queued"):
    with patch("app.api.routes.files.get_data_dir", return_value=tmp_path):
        f = await client.post("/api/v1/files/upload",
                              files={"file": ("m.3mf", _make_3mf(), "application/octet-stream")})
    file_id = f.json()["id"]
    p = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu", "connection_config": {},
        "orca_printer_profiles": ["X"], "current_orca_printer_profile": "X"})
    printer_id = p.json()["id"]
    with patch("app.api.routes.jobs.queue_engine"):
        j = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1, "order_id": order_id,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm"}]})
    return j.json()["id"]


async def test_create_order(client):
    resp = await _create_order(client)
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] is not None
    assert data["customer"] == "Vela Robotics"
    assert data["status"] == "queued"
    assert data["progress"] == 0.0
    assert data["job_count"] == 0
    assert data["parts"][0]["name"] == "Arm L"
    assert data["parts"][0]["id"]  # server-assigned part id


async def test_list_orders_empty(client):
    resp = await client.get("/api/v1/orders")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_order_not_found(client):
    assert (await client.get("/api/v1/orders/9999")).status_code == 404


async def test_status_in_progress_with_job(client, tmp_path):
    oid = (await _create_order(client)).json()["id"]
    await _make_job(client, tmp_path, oid)
    data = (await client.get(f"/api/v1/orders/{oid}")).json()
    assert data["status"] == "in_progress"
    assert data["job_count"] == 1
    assert data["progress"] == 0.0
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["plate_number"] == 1


async def test_hold_override(client):
    oid = (await _create_order(client)).json()["id"]
    resp = await client.patch(f"/api/v1/orders/{oid}", json={"on_hold": True})
    assert resp.status_code == 200
    assert resp.json()["status"] == "hold"


async def test_patch_replaces_parts(client):
    oid = (await _create_order(client)).json()["id"]
    resp = await client.patch(f"/api/v1/orders/{oid}", json={
        "parts": [{"name": "Clamp", "qty": 4, "material": "PETG", "est_minutes": 12}]})
    assert resp.status_code == 200
    parts = resp.json()["parts"]
    assert len(parts) == 1 and parts[0]["name"] == "Clamp" and parts[0]["id"]


async def test_delete_nulls_job_link(client, tmp_path):
    oid = (await _create_order(client)).json()["id"]
    job_id = await _make_job(client, tmp_path, oid)
    assert (await client.delete(f"/api/v1/orders/{oid}")).status_code == 204
    assert (await client.get(f"/api/v1/orders/{oid}")).status_code == 404
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["order_id"] is None
