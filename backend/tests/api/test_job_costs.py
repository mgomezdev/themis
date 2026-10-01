"""BIZ-184: a project's true cost — filament + machine time + labour + bought-in parts — at live shop rates."""
import pytest

from app.models import Job, UploadedFile
from app.services.job_costs import Rates, compute
from app.models import ProjectLabor, ProjectPart

HOUR = 3600


def _job(status="complete", seconds=HOUR, filament=None, printer=None):
    return Job(status=status, actual_seconds=seconds, filament_cost=filament, assigned_printer_id=printer)


def _labor(minutes):
    return ProjectLabor(minutes=minutes)


def _part(qty, unit):
    return ProjectPart(quantity=qty, unit_cost=unit)


# ---- the pure model ---------------------------------------------------------------------------

def test_compute_breaks_cost_into_filament_machine_labour_and_parts():
    jobs = [_job(seconds=2 * HOUR, filament=3.5), _job(seconds=HOUR // 2, filament=1.0)]
    out = compute(jobs, [_labor(45), _labor(15)], [_part(4, 0.25), _part(2, 1.5)], Rates(machine=2.0, labour=30.0))

    assert out == {
        "filament": 4.5,
        "machine": 5.0,                    # 2.5 h × $2
        "labour": 30.0,                    # 1 h × $30
        "parts": 4.0,                      # 4×0.25 + 2×1.5
        "machine_hours": 2.5, "labour_hours": 1.0,
        "total": 43.5,
    }


def test_only_completed_jobs_cost_machine_time():
    jobs = [_job("complete", HOUR), _job("failed", 5 * HOUR), _job("cancelled", 5 * HOUR), _job("printing", 5 * HOUR)]
    assert compute(jobs, [], [], Rates(machine=10.0))["machine"] == 10.0


def test_a_printers_own_rate_overrides_the_shop_rate_and_zero_is_a_real_override():
    rates = Rates(machine=10.0, printer_machine={1: 4.0, 2: 0.0})
    jobs = [_job(printer=1), _job(printer=2), _job(printer=3), _job(printer=None)]
    assert compute(jobs, [], [], rates)["machine"] == 24.0            # 4 + 0 (free machine) + 10 + 10


def test_parts_without_a_unit_cost_and_jobs_without_a_duration_add_nothing():
    out = compute([_job(seconds=None)], [], [_part(5, None), _part(2, 1.0)], Rates(machine=99.0))
    assert (out["machine"], out["parts"], out["total"]) == (0.0, 2.0, 2.0)


def test_nothing_recorded_costs_nothing():
    assert compute([], [], [], Rates())["total"] == 0.0


# ---- settings ---------------------------------------------------------------------------------

async def test_cost_settings_default_to_zero_and_roundtrip(client):
    assert (await client.get("/api/v1/settings/costs")).json() == {"machine_rate_per_hour": 0.0, "labour_rate_per_hour": 0.0}
    r = await client.put("/api/v1/settings/costs", json={"machine_rate_per_hour": 1.5, "labour_rate_per_hour": 25})
    assert r.json() == {"machine_rate_per_hour": 1.5, "labour_rate_per_hour": 25.0}
    assert (await client.get("/api/v1/settings/costs")).json() == {"machine_rate_per_hour": 1.5, "labour_rate_per_hour": 25.0}


@pytest.mark.parametrize("body", [{"machine_rate_per_hour": -1}, {"labour_rate_per_hour": -0.01}, {"machine_rate_per_hour": 1e9}])
async def test_invalid_rates_are_rejected(client, body):
    assert (await client.put("/api/v1/settings/costs", json=body)).status_code == 422


# ---- a project's costs end to end -------------------------------------------------------------

async def _project(client, **extra) -> dict:
    return (await client.post("/api/v1/projects", json={"name": "P", **extra})).json()


async def _complete_job(session_factory, project_id, *, seconds, filament=None, printer_id=None):
    async with session_factory() as s:
        f = UploadedFile(original_filename="m.3mf", stored_path="/x", plates=[], uploaded_at="2026-01-01T00:00:00")
        s.add(f)
        await s.flush()
        s.add(Job(uploaded_file_id=f.id, status="complete", project_id=project_id, actual_seconds=seconds,
                  filament_cost=filament, assigned_printer_id=printer_id, created_at="t", updated_at="t",
                  completed_at="2026-09-10T00:00:00"))
        await s.commit()


async def test_project_expenses_show_a_breakdown_and_follow_the_rates_live(client, session_factory):
    await client.put("/api/v1/settings/costs", json={"machine_rate_per_hour": 2.0, "labour_rate_per_hour": 30.0})
    p = await _project(client)
    await _complete_job(session_factory, p["id"], seconds=2 * HOUR, filament=3.0)
    assert (await client.post(f"/api/v1/projects/{p['id']}/labor", json={"minutes": 90, "note": "post-processing"})).status_code == 201
    await client.post(f"/api/v1/projects/{p['id']}/parts", json={"name": "magnet", "quantity": 10, "unit_cost": 0.4})

    costs = (await client.get(f"/api/v1/projects/{p['id']}")).json()["costs"]
    assert costs == {"filament": 3.0, "machine": 4.0, "labour": 45.0, "parts": 4.0,
                     "machine_hours": 2.0, "labour_hours": 1.5, "total": 56.0}

    # A rate change re-prices the past job straight away — nothing was locked in when it printed.
    await client.put("/api/v1/settings/costs", json={"machine_rate_per_hour": 5.0, "labour_rate_per_hour": 30.0})
    assert (await client.get(f"/api/v1/projects/{p['id']}")).json()["costs"]["machine"] == 10.0


async def test_a_printers_rate_override_prices_the_jobs_it_ran(client, session_factory, create_printer):
    await client.put("/api/v1/settings/costs", json={"machine_rate_per_hour": 2.0, "labour_rate_per_hour": 0})
    fast = await create_printer(name="Fast")
    await client.patch(f"/api/v1/printers/{fast}", json={"machine_rate_per_hour": 8.0})
    p = await _project(client)
    await _complete_job(session_factory, p["id"], seconds=HOUR, printer_id=fast)
    await _complete_job(session_factory, p["id"], seconds=HOUR)

    assert (await client.get(f"/api/v1/projects/{p['id']}")).json()["costs"]["machine"] == 10.0   # 8 + 2

    cleared = await client.patch(f"/api/v1/printers/{fast}", json={"machine_rate_per_hour": None})
    assert cleared.json()["machine_rate_per_hour"] is None                                          # back to the shop rate
    assert (await client.get(f"/api/v1/projects/{p['id']}")).json()["costs"]["machine"] == 4.0


async def test_printer_rate_validation(client, create_printer):
    pid = await create_printer()
    assert (await client.patch(f"/api/v1/printers/{pid}", json={"machine_rate_per_hour": -1})).status_code == 422


# ---- labour log -------------------------------------------------------------------------------

async def test_labour_log_lists_newest_first_and_deletes(client):
    p = await _project(client)
    a = (await client.post(f"/api/v1/projects/{p['id']}/labor", json={"minutes": 30, "logged_on": "2026-09-01"})).json()
    await client.post(f"/api/v1/projects/{p['id']}/labor", json={"minutes": 45, "logged_on": "2026-09-05", "note": " pack "})

    rows = (await client.get(f"/api/v1/projects/{p['id']}/labor")).json()
    assert [(r["logged_on"], r["minutes"], r["note"]) for r in rows] == [("2026-09-05", 45, "pack"), ("2026-09-01", 30, None)]

    assert (await client.delete(f"/api/v1/projects/{p['id']}/labor/{a['id']}")).json() == {"deleted": a["id"]}
    assert [r["minutes"] for r in (await client.get(f"/api/v1/projects/{p['id']}/labor")).json()] == [45]
    assert (await client.get(f"/api/v1/projects/{p['id']}")).json()["costs"]["labour_hours"] == 0.75


@pytest.mark.parametrize("body", [{"minutes": 0}, {"minutes": -5}, {"minutes": 1_000_000}, {"minutes": 5, "logged_on": "2999-01-01"}])
async def test_invalid_labour_is_rejected(client, body):
    p = await _project(client)
    assert (await client.post(f"/api/v1/projects/{p['id']}/labor", json=body)).status_code == 422
    assert (await client.get(f"/api/v1/projects/{p['id']}/labor")).json() == []


async def test_labour_404s_and_cross_project_ids(client):
    a, b = await _project(client), await _project(client)
    entry = (await client.post(f"/api/v1/projects/{a['id']}/labor", json={"minutes": 10})).json()
    assert (await client.get("/api/v1/projects/9999/labor")).status_code == 404
    assert (await client.post("/api/v1/projects/9999/labor", json={"minutes": 5})).status_code == 404
    assert (await client.delete(f"/api/v1/projects/{b['id']}/labor/{entry['id']}")).status_code == 404
    assert len((await client.get(f"/api/v1/projects/{a['id']}/labor")).json()) == 1


# ---- part unit costs --------------------------------------------------------------------------

async def test_part_unit_cost_can_be_set_changed_and_cleared(client):
    p = await _project(client)
    part = (await client.post(f"/api/v1/projects/{p['id']}/parts", json={"name": "screw", "quantity": 4, "unit_cost": 0.1})).json()
    assert part["unit_cost"] == 0.1

    r = await client.put(f"/api/v1/projects/{p['id']}/parts/{part['id']}", json={"unit_cost": 0.25})
    assert r.json()["unit_cost"] == 0.25
    assert (await client.put(f"/api/v1/projects/{p['id']}/parts/{part['id']}", json={"name": "bolt"})).json()["unit_cost"] == 0.25  # untouched
    assert (await client.put(f"/api/v1/projects/{p['id']}/parts/{part['id']}", json={"unit_cost": None})).json()["unit_cost"] is None
    assert (await client.put(f"/api/v1/projects/{p['id']}/parts/{part['id']}", json={"unit_cost": -1})).status_code == 422


# ---- customer financials ----------------------------------------------------------------------

async def test_customer_expenses_and_profit_use_the_full_cost_with_a_breakdown(client, session_factory):
    await client.put("/api/v1/settings/costs", json={"machine_rate_per_hour": 2.0, "labour_rate_per_hour": 30.0})
    c = (await client.post("/api/v1/customers", json={"name": "Acme", "email": "a@x.test"})).json()
    p = (await client.post("/api/v1/projects", json={"name": "Bench", "customer_id": c["id"], "price": 100,
                                                      "amount_paid": 100, "payment_status": "paid"})).json()
    await _complete_job(session_factory, p["id"], seconds=2 * HOUR, filament=3.0)
    await client.post(f"/api/v1/projects/{p['id']}/labor", json={"minutes": 30})
    await client.post(f"/api/v1/projects/{p['id']}/parts", json={"name": "magnet", "quantity": 5, "unit_cost": 1.0})

    d = (await client.get(f"/api/v1/customers/{c['id']}")).json()

    allw = d["financials"]["windows"]["all"]
    assert allw["expenses"] == 27.0                    # 3 filament + 4 machine + 15 labour + 5 parts
    assert allw["expense_breakdown"] == {"filament": 3.0, "machine": 4.0, "labour": 15.0, "parts": 5.0}
    assert allw["profit"] == 73.0                      # revenue 100 − full cost 27, not 100 − 3 filament
    assert d["projects"][0]["costs"]["total"] == 27.0
    assert d["projects"][0]["filament_cost_total"] == 3.0   # the filament-only figure is still available
