# backend/tests/api/test_orders_api.py


async def _create_order(client, **over):
    body = {
        "order_type": "customer", "customer": "Vela Robotics",
        "title": "Brackets", "due_date": "2026-06-01", "notes": "match black",
        "parts": [{"name": "Arm L", "qty": 8, "material": "PA-CF", "est_minutes": 78}],
    }
    body.update(over)
    return await client.post("/api/v1/orders", json=body)


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


async def test_status_in_progress_with_job(client, create_job):
    oid = (await _create_order(client)).json()["id"]
    await create_job(order_id=oid)
    data = (await client.get(f"/api/v1/orders/{oid}")).json()
    assert data["status"] == "in_progress"
    assert data["job_count"] == 1
    assert data["progress"] == 0.0
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["plate_number"] == 1


async def test_jobs_ordered_by_queue_position(client, create_job):
    oid = (await _create_order(client)).json()["id"]
    await create_job(order_id=oid)
    await create_job(order_id=oid)
    data = (await client.get(f"/api/v1/orders/{oid}")).json()
    assert len(data["jobs"]) == 2
    positions = [j["queue_position"] for j in data["jobs"]]
    assert positions == sorted(positions)  # ascending by queue_position
    # Each successive job gets the next queue_position, so ids come back in order.
    ids = [j["id"] for j in data["jobs"]]
    assert ids == sorted(ids)


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


async def test_delete_nulls_job_link(client, create_job):
    oid = (await _create_order(client)).json()["id"]
    job_id = await create_job(order_id=oid)
    assert (await client.delete(f"/api/v1/orders/{oid}")).status_code == 204
    assert (await client.get(f"/api/v1/orders/{oid}")).status_code == 404
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["order_id"] is None


async def test_payment_defaults(client):
    data = (await _create_order(client)).json()
    assert data["amount_paid"] is None
    assert data["payment_status"] == "unpaid"
    assert data["filament_cost_total"] is None


async def test_create_order_with_payment(client):
    data = (await _create_order(client, amount_paid=42.5, payment_status="paid")).json()
    assert data["amount_paid"] == 42.5
    assert data["payment_status"] == "paid"


async def test_patch_payment_status(client):
    oid = (await _create_order(client)).json()["id"]
    resp = await client.patch(f"/api/v1/orders/{oid}", json={"amount_paid": 10, "payment_status": "partial"})
    assert resp.status_code == 200
    assert resp.json()["amount_paid"] == 10.0
    assert resp.json()["payment_status"] == "partial"


async def test_invalid_payment_status_rejected(client):
    resp = await _create_order(client, payment_status="paid_in_full")
    assert resp.status_code == 422
    assert (await client.get("/api/v1/orders")).json() == []  # no order was created


async def test_filament_cost_total_aggregates_jobs(client, create_job):
    oid = (await _create_order(client)).json()["id"]
    job1 = await create_job(order_id=oid)
    job2 = await create_job(order_id=oid)
    await client.patch(f"/api/v1/jobs/{job1}/cost", json={"filament_cost": 3.5})
    await client.patch(f"/api/v1/jobs/{job2}/cost", json={"filament_cost": 1.25})
    data = (await client.get(f"/api/v1/orders/{oid}")).json()
    assert data["filament_cost_total"] == 4.75


async def test_filament_cost_total_zero_is_not_null(client, create_job):
    """A job explicitly costed at $0 must report a $0.00 total, not '—' (no data)."""
    oid = (await _create_order(client)).json()["id"]
    job_id = await create_job(order_id=oid)
    await client.patch(f"/api/v1/jobs/{job_id}/cost", json={"filament_cost": 0})
    data = (await client.get(f"/api/v1/orders/{oid}")).json()
    assert data["filament_cost_total"] == 0.0
