"""Weight-conflict guard (BIZ-198): a deduction computed from the print-start weight is HELD when the provider's weight was
changed meanwhile, and the user picks which weight to keep (use Themis' value / keep the provider's / subtract the job usage)."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.models import InventoryPendingWrite, Job, Printer, UploadedFile
from app.plugins.capabilities.filament_inventory import InventoryProviderError
from app.services.inventory import events, outbox
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import spool, use_provider

BASE = "/api/v1/inventory"
NOW = datetime.now(timezone.utc).isoformat()


async def _queue(session_factory, ref="1", pre=100.0, target=90.0, job_id=None):
    async with session_factory() as s:
        row = outbox.enqueue(s, "spoolman", ref, target, job_id=job_id, printer_id=None, source="queue", pre_weight_g=pre)
        await s.commit()
        return row.id


async def _row(session_factory, wid) -> InventoryPendingWrite:
    async with session_factory() as s:
        return await s.get(InventoryPendingWrite, wid)


async def _job(session_factory) -> int:
    async with session_factory() as s:
        s.add(Printer(id=1, name="P", printer_type="elegoo_centauri", connection_config={}))
        f = UploadedFile(original_filename="a", stored_path="/a", plates=[], uploaded_at=NOW)
        s.add(f)
        await s.flush()
        j = Job(uploaded_file_id=f.id, plate_number=1, queue_position=1.0, status="complete", created_at=NOW, updated_at=NOW)
        s.add(j)
        await s.commit()
        return j.id


async def _held(client, session_factory, provider_weight=80.0, **kw):
    """A spool the user re-weighed to `provider_weight` during the print, and the write held because of it."""
    fake = FakeInventoryProvider(spools=[spool("1", provider_weight)])
    await use_provider(fake)
    wid = await _queue(session_factory, **kw)
    assert (await client.post(f"{BASE}/pending-writes/flush")).json() == {"applied": 0}
    return fake, wid


async def test_an_unchanged_spool_gets_the_write(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    await use_provider(fake)
    wid = await _queue(session_factory)

    assert (await client.post(f"{BASE}/pending-writes/flush")).json() == {"applied": 1}
    assert fake.writes == [("1", 90.0)]
    assert (await _row(session_factory, wid)).status == "applied"


async def test_a_spool_already_at_the_target_is_marked_applied_without_a_write(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 90.0)])
    await use_provider(fake)
    wid = await _queue(session_factory)

    assert (await client.post(f"{BASE}/pending-writes/flush")).json() == {"applied": 1}
    assert fake.writes == []
    assert (await _row(session_factory, wid)).status == "applied"


async def test_a_weight_changed_in_the_provider_holds_the_write_and_raises_an_event(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 80.0)])                       # re-weighed to 80 g during the print
    await use_provider(fake)
    wid = await _queue(session_factory, job_id=None)

    with patch.object(events, "emit", new=AsyncMock(return_value=True)) as emit:
        assert (await client.post(f"{BASE}/pending-writes/flush")).json() == {"applied": 0}

    row = await _row(session_factory, wid)
    assert (row.status, row.conflict_current_g, row.target_g) == ("conflict", 80.0, 90.0)
    assert fake.writes == []                                                      # the re-weigh was NOT overwritten
    assert emit.await_args.args[1] == events.WEIGHT_CONFLICT
    assert emit.await_args.args[2]["found_g"] == 80.0 and emit.await_args.args[2]["expected_g"] == 100.0
    listed = (await client.get(f"{BASE}/pending-writes")).json()["items"][0]
    assert (listed["status"], listed["pre_weight_g"], listed["conflict_current_g"]) == ("conflict", 100.0, 80.0)


async def test_a_difference_within_rounding_is_not_a_conflict(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 99.7)])
    await use_provider(fake)
    await _queue(session_factory)

    assert (await client.post(f"{BASE}/pending-writes/flush")).json() == {"applied": 1}
    assert fake.writes == [("1", 90.0)]


async def test_two_queued_jobs_on_one_spool_are_not_a_conflict_whichever_step_landed(client, session_factory):
    for provider_weight in (100.0, 90.0):                                         # nothing landed yet / the first write landed
        fake = FakeInventoryProvider(spools=[spool("1", provider_weight)])
        await use_provider(fake)
        first = await _queue(session_factory, pre=100.0, target=90.0)
        second = await _queue(session_factory, pre=90.0, target=80.0)

        assert (await client.post(f"{BASE}/pending-writes/flush")).json() == {"applied": 1}
        assert fake.writes == [("1", 80.0)]
        assert (await _row(session_factory, first)).status == "superseded"
        assert (await _row(session_factory, second)).status == "applied"
        async with session_factory() as s:                                         # reset for the second round
            for r in (await s.execute(select(InventoryPendingWrite))).scalars():
                await s.delete(r)
            await s.commit()


async def test_a_row_without_a_start_weight_is_written_without_a_check(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 55.0)])
    await use_provider(fake)
    async with session_factory() as s:
        outbox.enqueue(s, "spoolman", "1", 40.0, job_id=None, printer_id=None, source="queue")
        await s.commit()

    assert (await client.post(f"{BASE}/pending-writes/flush")).json() == {"applied": 1}
    assert fake.writes == [("1", 40.0)]
    assert "get_spool" not in fake.calls


async def test_when_the_weight_cannot_be_read_the_write_stays_pending_and_is_retried(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    await use_provider(fake)
    fake.fail_with = InventoryProviderError("down", code="503", status=503)
    wid = await _queue(session_factory)

    assert (await client.post(f"{BASE}/pending-writes/flush")).json() == {"applied": 0}
    row = await _row(session_factory, wid)
    assert (row.status, row.attempts) == ("pending", 1) and row.last_error

    fake.fail_with = None
    assert (await client.post(f"{BASE}/pending-writes/flush")).json() == {"applied": 1}
    assert fake.writes == [("1", 90.0)]


async def test_a_held_conflict_blocks_later_writes_for_that_spool_only(client, session_factory):
    fake, wid = await _held(client, session_factory)
    fake.spools["2"] = spool("2", 50.0)
    later = await _queue(session_factory, ref="1", pre=80.0, target=70.0)
    other = await _queue(session_factory, ref="2", pre=50.0, target=40.0)

    assert (await client.post(f"{BASE}/pending-writes/flush")).json() == {"applied": 1}
    assert fake.writes == [("2", 40.0)]
    assert (await _row(session_factory, later)).status == "pending"
    assert (await _row(session_factory, wid)).status == "conflict"


async def test_resolving_with_themis_value_writes_the_computed_target(client, session_factory):
    fake, wid = await _held(client, session_factory)

    resolved = (await client.post(f"{BASE}/pending-writes/{wid}/resolve-conflict", json={"choice": "themis"})).json()

    assert (resolved["status"], resolved["target_g"]) == ("applied", 90.0)
    assert fake.writes == [("1", 90.0)]


async def test_resolving_with_the_provider_value_drops_the_deduction_and_flags_the_job(client, session_factory):
    job_id = await _job(session_factory)
    fake, wid = await _held(client, session_factory, job_id=job_id)

    resolved = (await client.post(f"{BASE}/pending-writes/{wid}/resolve-conflict", json={"choice": "provider"})).json()

    assert resolved["status"] == "discarded" and fake.writes == []
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        assert job.deduction_skipped and "kept" in job.deduction_note


async def test_resolving_with_subtract_applies_the_job_usage_to_the_providers_weight(client, session_factory):
    fake, wid = await _held(client, session_factory, provider_weight=80.0, pre=100.0, target=90.0)       # the job used 10 g

    resolved = (await client.post(f"{BASE}/pending-writes/{wid}/resolve-conflict", json={"choice": "subtract"})).json()

    assert (resolved["status"], resolved["target_g"]) == ("applied", 70.0)
    assert fake.writes == [("1", 70.0)]


async def test_resolve_conflict_rejects_unknown_rows_choices_and_rows_that_are_not_held(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    await use_provider(fake)
    wid = await _queue(session_factory)                                           # pending, not held

    assert (await client.post(f"{BASE}/pending-writes/{wid}/resolve-conflict", json={"choice": "themis"})).status_code == 404
    assert (await client.post(f"{BASE}/pending-writes/999/resolve-conflict", json={"choice": "themis"})).status_code == 404
    assert (await client.post(f"{BASE}/pending-writes/{wid}/resolve-conflict", json={"choice": "nope"})).status_code == 422
    assert (await _row(session_factory, wid)).status == "pending" and fake.writes == []


async def test_correcting_the_weight_by_hand_supersedes_a_held_conflict(client, session_factory):
    fake, wid = await _held(client, session_factory)

    assert (await client.put(f"{BASE}/spools/1/remaining", json={"remaining_g": 77.0})).status_code == 200

    assert (await _row(session_factory, wid)).status == "superseded"
    assert fake.writes == [("1", 77.0)]


async def test_a_completion_records_the_start_weight_the_write_was_computed_from(client, session_factory):
    from app.services.inventory import deduction, snapshots
    from app.models import JobSpoolSnapshot
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    await use_provider(fake)
    job_id = await _job(session_factory)
    async with session_factory() as s:
        s.add(JobSpoolSnapshot(job_id=job_id, printer_id=1, provider="spoolman", spool_ref="1", pre_weight_g=100.0,
                               source="live", taken_at=NOW))
        await s.commit()
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        plan = await deduction.plan_completion(s, job=job, printer_id=1, spool_ref="1", grams=12.0, source="queue")
        await s.commit()

    assert plan.kind == "enqueued"
    async with session_factory() as s:
        row = (await s.execute(select(InventoryPendingWrite))).scalar_one()
        assert (row.pre_weight_g, row.target_g) == (100.0, 88.0)


async def test_a_resolved_conflict_is_written_before_the_rows_queued_behind_it_and_those_are_checked_against_it(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 1200.0)])               # job A started at 1000, then the spool was re-weighed to 1200
    await use_provider(fake)
    a = await _queue(session_factory, pre=1000.0, target=900.0)
    await client.post(f"{BASE}/pending-writes/flush")
    b = await _queue(session_factory, pre=900.0, target=800.0)               # job B queued behind the held A
    assert (await _row(session_factory, a)).status == "conflict"

    resolved = (await client.post(f"{BASE}/pending-writes/{a}/resolve-conflict", json={"choice": "subtract"})).json()

    assert (resolved["status"], resolved["target_g"]) == ("applied", 1100.0)
    assert fake.writes == [("1", 1100.0)]                                    # the user's choice reached the provider, B did not replace it
    assert (await _row(session_factory, b)).status == "conflict"             # B was computed from 900; the spool is now 1100: asked again
    await client.post(f"{BASE}/pending-writes/{b}/resolve-conflict", json={"choice": "subtract"})
    assert fake.writes[-1] == ("1", 1000.0)                                  # 1100 - B's 100 g


async def test_subtract_on_a_held_chain_deducts_every_job_in_the_chain(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 120.0)])                 # re-weighed to 120 while two jobs were queued
    await use_provider(fake)
    first = await _queue(session_factory, pre=100.0, target=90.0)
    second = await _queue(session_factory, pre=90.0, target=80.0)
    await client.post(f"{BASE}/pending-writes/flush")

    held = (await client.get(f"{BASE}/pending-writes")).json()["items"]
    assert [(h["id"], h["status"], h["pre_weight_g"]) for h in held] == [(second, "conflict", 100.0)]      # the chain's start weight
    assert (await _row(session_factory, first)).status == "superseded"

    await client.post(f"{BASE}/pending-writes/{second}/resolve-conflict", json={"choice": "subtract"})

    assert fake.writes == [("1", 100.0)]                                     # 120 - (10 + 10)


async def test_a_held_write_of_another_provider_cannot_be_resolved_against_the_active_one(client, session_factory):
    fake, wid = await _held(client, session_factory)                         # held for "spoolman"
    await use_provider(FakeInventoryProvider(spools=[spool("1", 5.0)]), plugin_id="other_inventory")

    resp = await client.post(f"{BASE}/pending-writes/{wid}/resolve-conflict", json={"choice": "subtract"})

    assert resp.status_code == 409
    row = await _row(session_factory, wid)
    assert (row.status, row.target_g) == ("conflict", 90.0)                  # untouched: never baked another spool's weight in
    assert fake.writes == []


async def test_a_held_conflict_is_never_pruned_however_old(session_factory):
    async with session_factory() as s:
        for status in ("conflict", "applied"):
            s.add(InventoryPendingWrite(provider="spoolman", spool_ref="1", target_g=1.0, source="queue", status=status,
                                        created_at="2020-01-01T00:00:00+00:00"))
        await s.commit()

    await outbox._prune(session_factory)

    async with session_factory() as s:
        assert [r.status for r in (await s.execute(select(InventoryPendingWrite))).scalars()] == ["conflict"]
