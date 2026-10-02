"""GET /api/v1/fleet/analytics — aggregation rules documented in app/services/fleet_analytics.py."""
import pytest

from app.models import Job, JobPrinterConfig
from app.services.fleet_analytics import material_from_name

DAY = "2026-09-10"
AT = f"{DAY}T12:00:00"
RANGE = {"start": "2026-09-01", "end": "2026-09-30"}


async def _seed(session_factory, file_id, *, status="complete", printer_id=None, ran_on=None, completed_at=AT,
                seconds=None, grams=None, cost=None, breakdown=None, config=None):
    async with session_factory() as s:
        job = Job(uploaded_file_id=file_id, status=status, assigned_printer_id=printer_id,
                  printed_on_printer_id=ran_on, completed_at=completed_at, actual_seconds=seconds,
                  actual_filament_grams=grams, filament_cost=cost, actual_filament_breakdown=breakdown,
                  created_at=AT, updated_at=AT)
        s.add(job)
        await s.flush()
        if config:
            s.add(JobPrinterConfig(job_id=job.id, print_profile="0.20mm", filament_color="any", **config))
        await s.commit()
        return job.id


async def _get(client, **params):
    return await client.get("/api/v1/fleet/analytics", params={**RANGE, **params})


@pytest.fixture
async def farm(client, upload_3mf, create_printer):
    file_id = await upload_3mf()
    a = await create_printer(name="Alpha")
    b = await create_printer(name="Bravo")
    return file_id, a, b


async def test_empty_range_returns_zeroed_totals_and_every_printer(client, farm):
    _, a, b = farm
    body = (await _get(client)).json()

    assert body["range"] == {"start": "2026-09-01", "end": "2026-09-30", "days": 30}
    assert body["totals"] == {"completed": 0, "failed": 0, "cancelled": 0, "success_rate": None,
                              "print_seconds": 0, "filament_grams": 0.0, "filament_cost": None}
    assert [(p["printer_id"], p["name"], p["utilization_pct"]) for p in body["printers"]] == [
        (a, "Alpha", 0.0), (b, "Bravo", 0.0)]
    assert body["materials"] == []


async def test_totals_and_success_rate_exclude_cancelled(client, session_factory, farm):
    file_id, a, _ = farm
    for _i in range(3):
        await _seed(session_factory, file_id, printer_id=a, seconds=3600, grams=10.0, cost=1.5)
    await _seed(session_factory, file_id, status="failed", ran_on=a)
    await _seed(session_factory, file_id, status="cancelled")

    t = (await _get(client)).json()["totals"]

    assert (t["completed"], t["failed"], t["cancelled"]) == (3, 1, 1)
    assert t["success_rate"] == 75.0  # 3 / (3 + 1): the cancelled job is not an outcome
    assert t["print_seconds"] == 10800
    assert t["filament_grams"] == 30.0
    assert t["filament_cost"] == 4.5


async def test_only_completed_jobs_count_toward_time_grams_and_cost(client, session_factory, farm):
    file_id, a, _ = farm
    await _seed(session_factory, file_id, status="failed", ran_on=a, seconds=9999, grams=99.0, cost=9.0)
    await _seed(session_factory, file_id, status="cancelled", seconds=9999, grams=99.0, cost=9.0)

    t = (await _get(client)).json()["totals"]

    assert (t["print_seconds"], t["filament_grams"], t["filament_cost"]) == (0, 0.0, None)


async def test_cost_total_ignores_jobs_with_no_recorded_cost(client, session_factory, farm):
    file_id, a, _ = farm
    await _seed(session_factory, file_id, printer_id=a, cost=2.0)
    await _seed(session_factory, file_id, printer_id=a, cost=None)

    body = (await _get(client)).json()

    assert body["totals"]["filament_cost"] == 2.0
    assert body["printers"][0]["filament_cost"] == 2.0


async def test_failed_job_is_attributed_to_printer_it_ran_on_even_after_assignment_cleared(
        client, session_factory, farm):
    file_id, a, b = farm
    # assigned_printer_id is None on failure; printed_on_printer_id remembers where it ran.
    await _seed(session_factory, file_id, status="failed", printer_id=None, ran_on=a)
    await _seed(session_factory, file_id, status="complete", printer_id=b, ran_on=None)  # legacy row: fallback

    by_name = {p["name"]: p for p in (await _get(client)).json()["printers"]}

    assert (by_name["Alpha"]["failed"], by_name["Alpha"]["completed"], by_name["Alpha"]["success_rate"]) == (1, 0, 0.0)
    assert (by_name["Bravo"]["failed"], by_name["Bravo"]["completed"], by_name["Bravo"]["success_rate"]) == (0, 1, 100.0)


async def test_utilization_is_print_time_over_range_and_clamped(client, session_factory, farm):
    file_id, a, b = farm
    await _seed(session_factory, file_id, printer_id=a, seconds=86400)           # 1 day of a 10-day range
    await _seed(session_factory, file_id, printer_id=b, seconds=86400 * 30)      # more than the range

    body = (await _get(client, start="2026-09-01", end="2026-09-10")).json()
    util = {p["name"]: p["utilization_pct"] for p in body["printers"]}

    assert util == {"Alpha": 10.0, "Bravo": 100.0}


async def test_range_is_inclusive_of_both_end_days_and_uses_completed_at(client, session_factory, farm):
    file_id, a, _ = farm
    await _seed(session_factory, file_id, printer_id=a, completed_at="2026-09-01T00:00:00")
    await _seed(session_factory, file_id, printer_id=a, completed_at="2026-09-30T23:59:59+00:00")
    await _seed(session_factory, file_id, printer_id=a, completed_at="2026-08-31T23:59:59")
    await _seed(session_factory, file_id, printer_id=a, completed_at="2026-10-01T00:00:00")
    await _seed(session_factory, file_id, printer_id=a, completed_at=None)

    assert (await _get(client)).json()["totals"]["completed"] == 2


async def test_material_breakdown_prefers_per_extruder_profile_then_job_config(client, session_factory, farm):
    file_id, a, _ = farm
    await _seed(session_factory, file_id, printer_id=a, grams=30.0, breakdown=[
        {"extruder_index": 0, "filament_profile": "Bambu PLA Basic @BBL X1C", "grams": 20.0},
        {"extruder_index": 1, "filament_profile": "Generic PETG", "grams": 10.0}])
    await _seed(session_factory, file_id, printer_id=a, grams=5.0, config={"printer_id": a, "filament_type": "ABS"})
    await _seed(session_factory, file_id, printer_id=a, grams=2.0,
                config={"printer_id": a, "filament_type": "any", "filament_profile": "Elegoo TPU 95A"})
    await _seed(session_factory, file_id, printer_id=a, grams=1.0)  # nothing to name it by

    mats = (await _get(client)).json()["materials"]

    assert mats == [
        {"material": "PLA", "grams": 20.0, "jobs": 1},
        {"material": "PETG", "grams": 10.0, "jobs": 1},
        {"material": "ABS", "grams": 5.0, "jobs": 1},
        {"material": "TPU", "grams": 2.0, "jobs": 1},
        {"material": "Unknown", "grams": 1.0, "jobs": 1},
    ]


async def test_defaults_to_the_last_30_days_ending_today(client):
    body = (await client.get("/api/v1/fleet/analytics")).json()
    assert body["range"]["days"] == 30


@pytest.mark.parametrize("params", [
    {"start": "2026-09-10", "end": "2026-09-09"},
    {"start": "2025-01-01", "end": "2026-09-30"},
])
async def test_bad_ranges_are_422(client, params):
    assert (await client.get("/api/v1/fleet/analytics", params=params)).status_code == 422


@pytest.mark.parametrize("name, expected", [
    ("Bambu PLA Basic @BBL X1C", "PLA"), ("Generic PETG-CF", "PETG"), ("eSun PLA+", "PLA"),
    ("Polymaker Nylon", "PA"), ("Bambu ABS", "ABS"), ("Prusament PC Blend", "PC"),
    ("Mystery Filament", None), ("", None), (None, None),
])
def test_material_from_name(name, expected):
    assert material_from_name(name) == expected

