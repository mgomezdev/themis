"""Deduction-model routes (BIZ-218): pending writes (list / flush / discard / resolve), suspended tracking and resuming it."""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from tests.webhook_helpers import destination
from app.models import InventoryPendingWrite, InventorySpoolStatus, Job, JobSpoolSnapshot, Printer, UploadedFile
from app.plugins.capabilities.filament_inventory import InventoryProviderError, TRACKS_WEIGHT
from app.services.inventory import deduction, outbox, tasks
from tests.api.test_inventory_api import _client_with
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import spool, use_provider

BASE = "/api/v1/inventory"
NOW = datetime.now(timezone.utc).isoformat()


async def _queue(session_factory, ref="1", target=90.0):
    async with session_factory() as s:
        row = outbox.enqueue(s, "spoolman", ref, target, job_id=None, printer_id=None, source="queue")
        await s.commit()
        return row.id


async def _suspend(session_factory, ref="1"):
    async with session_factory() as s:
        s.add(Printer(id=1, name="P", printer_type="elegoo_centauri", connection_config={}))
        f = UploadedFile(original_filename="a", stored_path="/a", plates=[], uploaded_at=NOW)
        s.add(f)
        await s.flush()
        j = Job(uploaded_file_id=f.id, plate_number=1, queue_position=1.0, status="complete", created_at=NOW, updated_at=NOW)
        s.add(j)
        await s.flush()
        await deduction.suspend(s, "spoolman", ref, "No starting weight", j)
        await s.commit()
        return j.id


async def test_pending_writes_list_flush_and_the_applied_row_leaves_the_list(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    await use_provider(fake)
    wid = await _queue(session_factory)

    listed = (await client.get(f"{BASE}/pending-writes")).json()
    assert listed["provider"] == "spoolman"
    assert [(i["id"], i["spool_ref"], i["target_g"], i["status"]) for i in listed["items"]] == [(wid, "1", 90.0, "pending")]

    assert (await client.post(f"{BASE}/pending-writes/flush")).json() == {"applied": 1}
    assert fake.writes == [("1", 90.0)]
    assert (await client.get(f"{BASE}/pending-writes")).json()["items"] == []


async def test_discarding_a_pending_write_means_it_is_never_sent(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    await use_provider(fake)
    wid = await _queue(session_factory)

    assert (await client.post(f"{BASE}/pending-writes/{wid}/discard")).json()["status"] == "discarded"
    await client.post(f"{BASE}/pending-writes/flush")

    assert fake.writes == []
    assert (await client.post(f"{BASE}/pending-writes/{wid}/discard")).status_code == 404      # already gone
    assert (await client.post(f"{BASE}/pending-writes/999/resolve")).status_code == 404


async def test_resolve_retries_with_a_corrected_target(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    await use_provider(fake)
    fake.fail_with = InventoryProviderError("down", code="503", status=503)
    wid = await _queue(session_factory)
    await client.post(f"{BASE}/pending-writes/flush")                                           # fails: stays pending
    assert (await client.get(f"{BASE}/pending-writes")).json()["items"][0]["last_error"]

    fake.fail_with = None
    resolved = (await client.post(f"{BASE}/pending-writes/{wid}/resolve", json={"target_g": 75.0})).json()

    assert (resolved["status"], resolved["target_g"]) == ("applied", 75.0)
    assert fake.writes == [("1", 75.0)]


async def test_flush_and_resolve_need_a_provider_that_writes_weight(client, session_factory):
    await use_provider(FakeInventoryProvider(capabilities=frozenset({TRACKS_WEIGHT})))
    wid = await _queue(session_factory)
    for r in (await client.post(f"{BASE}/pending-writes/flush"), await client.post(f"{BASE}/pending-writes/{wid}/resolve")):
        assert r.status_code == 409 and r.json()["error"] == "capability_unavailable"


async def test_the_tracking_list_shows_suspended_spools(client, session_factory):
    await use_provider(FakeInventoryProvider(spools=[spool("1", 100.0)]))
    assert (await client.get(f"{BASE}/tracking")).json()["items"] == []
    job_id = await _suspend(session_factory)

    (item,) = (await client.get(f"{BASE}/tracking")).json()["items"]
    assert (item["spool_ref"], item["reason"], item["job_id"]) == ("1", "No starting weight", job_id)


async def test_resume_tracking_with_a_corrected_weight_writes_it_clears_the_suspension_and_drops_stale_writes(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    await use_provider(fake)
    await _suspend(session_factory)
    stale = await _queue(session_factory, target=40.0)                  # computed from the old, wrong weight
    async with session_factory() as s:
        s.add(destination(url="http://hook.test", secret=None, events=[]))
        await s.commit()

    with patch("app.services.webhook_service.schedule") as hook:
        resp = await client.post(f"{BASE}/spools/1/resume-tracking", json={"remaining_g": 321.0})

    assert resp.status_code == 200 and resp.json()["tracking"] == "ok"
    assert fake.writes == [("1", 321.0)]
    assert [c.args[2] for c in hook.call_args_list] == ["inventory.tracking_restored"]
    assert (await client.get(f"{BASE}/tracking")).json()["items"] == []
    async with session_factory() as s:
        assert (await s.get(InventoryPendingWrite, stale)).status == "superseded"
    assert (await client.post(f"{BASE}/spools/1/resume-tracking")).status_code == 404      # nothing left to resume


async def test_resume_tracking_without_a_weight_just_confirms_the_current_one(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    await use_provider(fake)
    await _suspend(session_factory)

    assert (await client.post(f"{BASE}/spools/1/resume-tracking")).status_code == 200
    assert fake.writes == []
    async with session_factory() as s:
        assert (await s.execute(select(InventorySpoolStatus))).first() is None


async def test_setting_a_weight_by_hand_also_resumes_tracking(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    await use_provider(fake)
    await _suspend(session_factory)

    assert (await client.put(f"{BASE}/spools/1/remaining", json={"remaining_g": 55.0})).status_code == 200

    assert (await client.get(f"{BASE}/tracking")).json()["items"] == []


async def test_these_routes_need_the_inventory_scopes(client, session_factory):
    await use_provider(FakeInventoryProvider(spools=[spool("1", 100.0)]))
    wid = await _queue(session_factory)
    reader = await _client_with(session_factory, ["inventory:read"])
    async with reader:
        assert (await reader.get(f"{BASE}/pending-writes")).status_code == 200
        assert (await reader.get(f"{BASE}/tracking")).status_code == 200
        assert (await reader.post(f"{BASE}/pending-writes/flush")).status_code == 403
        assert (await reader.post(f"{BASE}/pending-writes/{wid}/discard")).status_code == 403
        assert (await reader.post(f"{BASE}/spools/1/resume-tracking")).status_code == 403


class _SlowFake(FakeInventoryProvider):
    """The FIRST set_remaining parks until released (a flush held 'in flight'); later ones go straight through."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        import asyncio
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self._first = True

    async def set_remaining(self, spool_ref, remaining_g):
        if self._first:
            self._first = False
            self.entered.set()
            await self.release.wait()
        await super().set_remaining(spool_ref, remaining_g)


async def test_a_manual_weight_waits_for_an_in_flight_flush_so_it_always_lands_last(client, session_factory):
    import asyncio
    fake = _SlowFake(spools=[spool("1", 100.0)])
    await use_provider(fake)
    await _queue(session_factory, target=40.0)

    flush = asyncio.create_task(outbox.flush(session_factory))
    await asyncio.wait_for(fake.entered.wait(), 5)                      # the stale 40 g write is in flight
    put = asyncio.create_task(client.put(f"{BASE}/spools/1/remaining", json={"remaining_g": 321.0}))
    await asyncio.sleep(0.05)
    assert not put.done()                                               # held back by the flush lock
    fake.release.set()
    await flush
    assert (await put).status_code == 200

    assert fake.writes == [("1", 40.0), ("1", 321.0)]                   # the user's value is the last word
    assert fake.spools["1"].remaining_g == 321.0


async def test_discard_waits_for_an_in_flight_send_of_the_same_row(client, session_factory):
    import asyncio
    fake = _SlowFake(spools=[spool("1", 100.0)])
    await use_provider(fake)
    wid = await _queue(session_factory, target=40.0)

    flush = asyncio.create_task(outbox.flush(session_factory))
    await asyncio.wait_for(fake.entered.wait(), 5)
    discard = asyncio.create_task(client.post(f"{BASE}/pending-writes/{wid}/discard"))
    await asyncio.sleep(0.05)
    assert not discard.done()
    fake.release.set()
    await flush

    assert (await discard).status_code == 404                           # it was already applied: nothing left to discard
    async with session_factory() as s:
        assert (await s.get(InventoryPendingWrite, wid)).status == "applied"
