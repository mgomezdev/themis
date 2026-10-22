"""A finished print deducts the spool through the ACTIVE inventory provider, driven by events (BIZ-308; design decisions D6, D11).

Real routes, queue engine, printer manager and a virtual (mock-plugin) printer; the Local inventory plugin as the active provider
(no Spoolman, no Laminus). The queue and the printer never touch inventory: they publish `job.started` / `job.complete` and the
inventory observer (`services/inventory/subscribers.py`) snapshots the bound spool at the start and writes `snapshot - spent` at the end.
Fails if either subscriber is removed."""
import pytest
from sqlalchemy import select, text

from app.eventing import EventEnvelope
from app.eventing.hub import hub
from app.models import EventOutbox, Job, Printer
from app.plugins.capabilities.filament_inventory import CAPABILITY
from app.services.inventory import subscribers
from app.services.printer_manager import printer_manager
from tests.test_workflow_end_to_end import farm, run_cycle          # noqa: F401  (fixture)
from tests.waiting import settle_events

INV = "/api/v1/inventory"


async def _local_inventory_active(client) -> None:
    assert (await client.put("/api/v1/plugins/local_inventory", json={"enabled": True})).status_code == 200
    assert (await client.put(f"/api/v1/capabilities/{CAPABILITY}/provider", json={"plugin_id": "local_inventory"})).status_code == 200


async def _spool(client, grams: float) -> str:
    material = (await client.post(f"{INV}/materials", json={"name": "PLA", "material": "PLA"})).json()
    spool = await client.post(f"{INV}/spools", json={"material_ref": material["ref"], "initial_g": grams})
    assert spool.status_code in (200, 201), spool.text
    return spool.json()["ref"]


async def _printer_with_slot(client, session_factory, spool_ref: str | None, binding: dict | None = None) -> int:
    created = await client.post("/api/v1/printers", json={
        "name": "Mock", "printer_type": "mock", "connection_config": {},
        "orca_printer_profiles": ["Mock Machine"], "machine_profile_override": "Mock Machine"})
    assert created.status_code == 201, created.text
    printer_id = created.json()["id"]
    async with session_factory() as s:                                    # the spool in slot 0, as the operator bound it on the Fleet page
        (await s.get(Printer, printer_id)).loaded_filaments = [{
            "slot": 0, "type": "PLA", "subtype": "standard", "color": "#FF0000", "reported": True,
            **({"inventory": binding or {"provider": "local_inventory", "spool_ref": spool_ref}} if (binding or spool_ref) else {})}]
        await s.commit()
    return printer_id


async def _queue_job(client, upload_3mf, printer_id: int) -> int:
    resp = await client.post("/api/v1/jobs", json={
        "uploaded_file_id": await upload_3mf(), "plate_number": 1,
        "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm", "filament_type": "PLA", "filament_subtype": "standard", "filament_color": "any"}]})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _print_to_completion(client, farm, session_factory, printer_id: int, job_id: int) -> None:
    await run_cycle(farm.engine)
    assert (await client.get(f"/api/v1/jobs/{job_id}")).json()["status"] == "printing"
    await settle_events(session_factory)                                  # `job.started` -> the start snapshot
    printer_manager.get_client(printer_id).stop_print()
    await printer_manager.on_print_complete(printer_id, None)
    await settle_events(session_factory)                                  # `job.complete` -> the deduction


async def _remaining(client, ref: str) -> float:
    spools = (await client.get(f"{INV}/spools")).json()["items"]
    return next(x for x in spools if x["ref"] == ref)["remaining_g"]


async def _rows(factory, sql: str) -> list[dict]:
    async with factory() as s:
        return [dict(r) for r in (await s.execute(text(sql))).mappings().all()]


async def test_a_finished_print_sets_the_spool_to_snapshot_minus_spent_and_a_resent_completion_changes_nothing(
        client, farm, session_factory, upload_3mf):
    await _local_inventory_active(client)
    ref = await _spool(client, 500.0)
    printer_id = await _printer_with_slot(client, session_factory, ref)
    job_id = await _queue_job(client, upload_3mf, printer_id)

    await _print_to_completion(client, farm, session_factory, printer_id, job_id)

    # the print used 12.5 g (the sliced gcode's estimate): the provider holds the ABSOLUTE weight, with its audit row
    assert await _remaining(client, ref) == 487.5
    assert [r["new_g"] for r in await _rows(session_factory, "SELECT new_g FROM local_inv_weight_log ORDER BY id")][-1] == 487.5
    assert [(r["pre_weight_g"], r["source"]) for r in await _rows(
        session_factory, "SELECT pre_weight_g, source FROM job_spool_snapshots")] == [(500.0, "live")]
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["status"] == "complete" and job["deduction_skipped"] is False

    # D6: the completion arriving again (at-least-once delivery) is harmless
    async with session_factory() as s:
        stored = (await s.execute(select(EventOutbox).where(EventOutbox.name == "job.complete"))).scalar_one()
        envelope = EventEnvelope.model_validate(stored.envelope)
    await subscribers.deduct_on_complete(envelope)
    await settle_events(session_factory)
    assert await _remaining(client, ref) == 487.5
    assert len(await _rows(session_factory, "SELECT id FROM inventory_pending_writes")) == 1


async def test_a_spool_with_no_obtainable_start_weight_is_suspended_and_its_next_print_is_skipped(
        client, farm, session_factory, upload_3mf):
    await _local_inventory_active(client)
    printer_id = await _printer_with_slot(client, session_factory, "9999")        # bound to a spool the provider has never heard of
    first = await _queue_job(client, upload_3mf, printer_id)

    await _print_to_completion(client, farm, session_factory, printer_id, first)

    job = (await client.get(f"/api/v1/jobs/{first}")).json()
    assert job["status"] == "complete" and job["deduction_skipped"] is True and "starting weight" in job["deduction_note"]
    assert [(r["spool_ref"], r["tracking"]) for r in await _rows(
        session_factory, "SELECT spool_ref, tracking FROM inventory_spool_status")] == [("9999", "suspended")]
    assert await _rows(session_factory, "SELECT id FROM inventory_pending_writes") == []

    # the next print on that spool is skipped and flagged, until someone corrects the weight
    assert (await client.post(f"/api/v1/printers/{printer_id}/plate-cleared")).status_code == 200
    second = await _queue_job(client, upload_3mf, printer_id)
    await _print_to_completion(client, farm, session_factory, printer_id, second)
    job = (await client.get(f"/api/v1/jobs/{second}")).json()
    assert job["status"] == "complete" and job["deduction_skipped"] is True and "suspended" in job["deduction_note"]
    assert await _rows(session_factory, "SELECT id FROM inventory_pending_writes") == []


@pytest.mark.parametrize("binding", [
    None,                                                              # nothing loaded against the library: no ref at all
    {"provider": "local_inventory", "spool_ref": ""},                  # a binding with a blank ref
    {"provider": "spoolman", "spool_ref": "1"},                        # a ref that belongs to a different provider
], ids=["no-binding", "blank-ref", "other-provider"])
async def test_a_slot_with_no_spool_ref_the_library_can_use_changes_nothing_and_is_flagged_not_failed(
        client, farm, session_factory, upload_3mf, binding):
    await _local_inventory_active(client)
    ref = await _spool(client, 500.0)                                  # a library spool exists, but this slot is not bound to it
    printer_id = await _printer_with_slot(client, session_factory, None, binding)
    job_id = await _queue_job(client, upload_3mf, printer_id)

    await _print_to_completion(client, farm, session_factory, printer_id, job_id)

    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["status"] == "complete" and job["deduction_skipped"] is True and "No spool is bound" in job["deduction_note"]
    assert await _remaining(client, ref) == 500.0                      # the library item is untouched
    assert await _rows(session_factory, "SELECT id FROM inventory_pending_writes") == []
    assert await _rows(session_factory, "SELECT id FROM job_spool_snapshots") == []
    assert await _rows(session_factory, "SELECT spool_ref FROM inventory_spool_status") == []        # nothing suspended: there is no spool to blame
    assert (await client.get(f"{INV}/spools")).status_code == 200      # and the library keeps working


async def test_a_start_snapshot_delivered_after_the_print_ended_is_not_taken(client, farm, session_factory, upload_3mf):
    """`job.started` and `job.complete` are separate lanes with no ordering: if the start event is handled late, the weight now is
    not the start weight, so no snapshot is stored (the completion already took the deferred path and deducted once)."""
    await _local_inventory_active(client)
    ref = await _spool(client, 500.0)
    printer_id = await _printer_with_slot(client, session_factory, ref)
    job_id = await _queue_job(client, upload_3mf, printer_id)
    await run_cycle(farm.engine)
    printer_manager.get_client(printer_id).stop_print()
    await printer_manager.on_print_complete(printer_id, None)            # complete BEFORE the start event is delivered
    async with session_factory() as s:
        started = EventEnvelope.model_validate((await s.execute(select(EventOutbox).where(EventOutbox.name == "job.started"))).scalar_one().envelope)
    await subscribers.deduct_on_complete(EventEnvelope.model_validate(
        (await _outbox_envelope(session_factory, "job.complete"))))
    await settle_events(session_factory)
    assert await _remaining(client, ref) == 487.5

    await subscribers.snapshot_on_start(started)                         # the late start event

    assert await _rows(session_factory, "SELECT id FROM job_spool_snapshots") == []
    assert await _remaining(client, ref) == 487.5


async def _outbox_envelope(factory, name: str) -> dict:
    async with factory() as s:
        return (await s.execute(select(EventOutbox).where(EventOutbox.name == name))).scalar_one().envelope


async def _completed_job_with_snapshot(client, farm, session_factory, upload_3mf):
    await _local_inventory_active(client)
    ref = await _spool(client, 500.0)
    printer_id = await _printer_with_slot(client, session_factory, ref)
    job_id = await _queue_job(client, upload_3mf, printer_id)
    return ref, printer_id, job_id


async def _legacy_complete(factory, job_id: int, printer_id: int, **payload) -> None:
    """A `job.complete` written by the previous release: the publisher had already resolved the spools (`inventory`, `deduction_notes`)."""
    async with factory() as s:
        env = EventEnvelope(name="job.complete", schema_version=2, entities={"job_id": job_id, "printer_id": printer_id}, dedup_key=f"legacy:{job_id}",
                            payload={"source": "queue", "actual_seconds": 60, "actual_grams": 12.5, **payload})
        await hub.enqueue_durable(s, env)
        await s.commit()
    await settle_events(factory)


@pytest.mark.parametrize("shape", ["one-dict", "list"])
async def test_a_legacy_job_complete_with_resolved_spools_is_still_deducted(client, farm, session_factory, upload_3mf, shape):
    ref, printer_id, job_id = await _completed_job_with_snapshot(client, farm, session_factory, upload_3mf)
    await run_cycle(farm.engine)
    await settle_events(session_factory)                                 # snapshot at 500
    async with session_factory() as s:                                   # the job ended without the new event (it predates this release)
        (await s.get(Job, job_id)).status = "complete"
        await s.commit()
    use = {"spool_ref": ref, "grams": 20.0}
    await _legacy_complete(session_factory, job_id, printer_id, inventory=use if shape == "one-dict" else [use], deduction_notes=[])

    assert await _remaining(client, ref) == 480.0


async def test_a_legacy_job_complete_with_nothing_to_deduct_does_not_invent_a_warning(client, farm, session_factory, upload_3mf):
    ref, printer_id, job_id = await _completed_job_with_snapshot(client, farm, session_factory, upload_3mf)
    async with session_factory() as s:
        (await s.get(Job, job_id)).status = "complete"
        await s.commit()
    await _legacy_complete(session_factory, job_id, printer_id, inventory=None, deduction_notes=[])        # old "deduction did not apply"

    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert not job["deduction_skipped"] and not job["deduction_note"]
    assert await _remaining(client, ref) == 500.0


async def test_a_legacy_job_complete_with_notes_flags_the_job(client, farm, session_factory, upload_3mf):
    ref, printer_id, job_id = await _completed_job_with_snapshot(client, farm, session_factory, upload_3mf)
    async with session_factory() as s:
        (await s.get(Job, job_id)).status = "complete"
        await s.commit()
    await _legacy_complete(session_factory, job_id, printer_id, inventory=None, deduction_notes=["No spool is bound to T0, so nothing"])

    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["deduction_skipped"] is True and "No spool is bound to T0" in job["deduction_note"]


async def test_each_start_is_its_own_durable_event_and_the_same_start_is_one(client, farm, session_factory, upload_3mf):
    from app.services import completion_events
    _, printer_id, job_id = await _completed_job_with_snapshot(client, farm, session_factory, upload_3mf)
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.updated_at = "2026-01-01T00:00:00+00:00"
        assert await completion_events.enqueue_job_started(s, job, printer_id, loaded_filaments=[]) is True
        assert await completion_events.enqueue_job_started(s, job, printer_id, loaded_filaments=[]) is False       # same start: one event
        job.updated_at = "2026-01-01T00:05:00+00:00"                                                                # restarted later
        assert await completion_events.enqueue_job_started(s, job, printer_id, loaded_filaments=[]) is True
        await s.commit()
    async with session_factory() as s:
        assert len((await s.execute(select(EventOutbox).where(EventOutbox.name == "job.started"))).scalars().all()) == 2


def test_the_queue_engine_and_the_job_routes_import_nothing_from_inventory_services_but_reads():
    """The printer/queue side publishes events; it does not drive spool tracking. (Reading the library to show it is fine.)"""
    import ast
    import app.api.routes.jobs as jobs
    import app.services.queue_engine as queue_engine
    allowed = {"read", "refs"}                                             # job views and the preflight read spools; they never write or snapshot
    for module in (queue_engine, jobs):
        tree = ast.parse(open(module.__file__).read())
        used = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("services.inventory")
                or isinstance(n, ast.ImportFrom) and n.module == "inventory" for a in n.names}
        used |= {(n.module or "").rsplit(".", 1)[-1] for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom) and ".inventory." in f".{n.module}." and (n.module or "").split(".")[-2:-1] == ["inventory"]}
        assert used <= allowed | {"preflight"}, f"{module.__name__} reaches into inventory: {sorted(used - allowed)}"


async def _finish_with_details(client, farm, session_factory, upload_3mf, details):
    """A print started by the queue (snapshot at 500 g) and finished through the engine's completion path with the given details."""
    ref, printer_id, job_id = await _completed_job_with_snapshot(client, farm, session_factory, upload_3mf)
    await run_cycle(farm.engine)
    await settle_events(session_factory)
    assert await farm.engine.complete_job(job_id, printer_id, origin=None, details=details) is True
    await settle_events(session_factory)
    return ref


async def test_the_filament_usage_detail_wins_over_the_grams_on_the_event(client, farm, session_factory, upload_3mf):
    ref = await _finish_with_details(client, farm, session_factory, upload_3mf, {"filament_usage": {"grams": 20.0, "breakdown": None}})
    assert await _remaining(client, ref) == 480.0                         # the reported 20 g, not the 12.5 g on the event


async def test_without_a_usable_detail_the_event_grams_are_used(client, farm, session_factory, upload_3mf):
    from app.services.job_details import UNSUPPORTED
    ref = await _finish_with_details(client, farm, session_factory, upload_3mf, {"filament_usage": UNSUPPORTED})
    assert await _remaining(client, ref) == 487.5                         # the printer says it cannot report usage: the payload shape from before details
