"""Durable delivery (BIZ-249): the outbox is written in the publisher's transaction, delivered at least once, retried, and
survives a subscriber being disabled; a failing subscriber never blocks another."""
import asyncio

import pytest
from sqlalchemy import select

from app.eventing import EventEnvelope, EventError
from app.eventing import hub as hubmod
from app.eventing.hub import EventHub
from app.models import EventDelivery, EventOutbox
from app.plugins.host import plugin_host
from tests.eventing.fakes import definer_manifest, enable, register, subscriber_manifest
from tests.waiting import wait_until


def complete(job_id: int = 1) -> EventEnvelope:
    return EventEnvelope(name="job.complete", entities={"job_id": job_id}, dedup_key=f"job.complete:{job_id}")


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(hubmod, "BACKOFF_BASE_S", 0.02)
    monkeypatch.setattr(hubmod, "DORMANT_RECHECK_S", 0.05)


@pytest.fixture
async def hub(session_factory):
    await plugin_host.start()
    h = EventHub()
    await h.start(session_factory)
    yield h
    await h.stop()


async def publish(hub, session_factory, envelope) -> bool:
    async with session_factory() as s:
        stored = await hub.enqueue_durable(s, envelope)
        await s.commit()
    hub.wake()
    return stored


async def deliveries(session_factory) -> list[EventDelivery]:
    async with session_factory() as s:
        return list((await s.execute(select(EventDelivery).order_by(EventDelivery.id))).scalars())


async def test_a_committed_durable_event_is_delivered_and_marked_delivered(hub, session_factory):
    got = []

    async def handler(e):
        got.append(e)

    hub.subscribe("job.complete", handler, name="t.deduct")
    event = complete(4)
    assert await publish(hub, session_factory, event) is True

    await wait_until(lambda: got, what="delivery")
    (d,) = await wait_until(lambda: _delivered(session_factory), what="delivered row")
    assert got[0] == event and (d.subscriber, d.attempts, d.last_error) == ("core:t.deduct", 1, None)
    assert d.delivered_at is not None


async def _delivered(session_factory):
    rows = [d for d in await deliveries(session_factory) if d.status == "delivered"]
    return rows or None


async def test_a_rolled_back_transaction_publishes_nothing(hub, session_factory):
    got = []

    async def handler(e):
        got.append(e)

    hub.subscribe("job.complete", handler, name="t.rollback")
    async with session_factory() as s:
        await hub.enqueue_durable(s, complete(1))
        await s.rollback()
    hub.wake()
    await asyncio.sleep(0.1)
    assert got == [] and await deliveries(session_factory) == []
    async with session_factory() as s:
        assert (await s.execute(select(EventOutbox))).first() is None


async def test_the_same_logical_event_is_stored_once_and_delivered_once(hub, session_factory):
    got = []

    async def handler(e):
        got.append(e.id)

    hub.subscribe("job.complete", handler, name="t.dedup")
    first = complete(9)
    assert await publish(hub, session_factory, first) is True
    assert await publish(hub, session_factory, complete(9)) is False                 # a duplicate completion callback
    await wait_until(lambda: got, what="delivery")
    await asyncio.sleep(0.1)
    assert got == [first.id] and len(await deliveries(session_factory)) == 1
    async with session_factory() as s:
        assert len((await s.execute(select(EventOutbox))).all()) == 1


async def test_a_duplicate_does_not_poison_the_callers_transaction(hub, session_factory):
    async def handler(e): ...

    hub.subscribe("job.complete", handler, name="t.poison")
    await publish(hub, session_factory, complete(1))
    async with session_factory() as s:
        assert await hub.enqueue_durable(s, complete(1)) is False
        assert await hub.enqueue_durable(s, complete(2)) is True                    # same transaction carries on
        await s.commit()
    async with session_factory() as s:
        assert {o.dedup_key for o in (await s.execute(select(EventOutbox))).scalars()} == {"job.complete:1", "job.complete:2"}


async def test_a_failed_delivery_is_retried_with_the_same_envelope_until_it_succeeds(hub, session_factory):
    seen = []

    async def flaky(e):
        seen.append(e.id)
        if len(seen) < 3:
            raise RuntimeError("provider down")

    hub.subscribe("job.complete", flaky, name="t.flaky")
    event = complete(1)
    await publish(hub, session_factory, event)

    (d,) = await wait_until(lambda: _delivered(session_factory), what="eventual delivery")
    assert seen == [event.id] * 3 and d.attempts == 3                                # at-least-once; the id is stable for idempotency


async def test_a_delivery_that_always_fails_goes_dead_and_an_operator_retry_revives_it(hub, session_factory, monkeypatch):
    monkeypatch.setattr(hubmod, "MAX_ATTEMPTS", 3)
    mode = {"fail": True}
    calls = []

    async def handler(e):
        calls.append(e.id)
        if mode["fail"]:
            raise RuntimeError("password=hunter2 rejected")

    hub.subscribe("job.complete", handler, name="t.dead")
    await publish(hub, session_factory, complete(1))

    async def dead():
        return [d for d in await deliveries(session_factory) if d.status == "dead"] or None

    (d,) = await wait_until(dead, what="dead delivery")
    assert d.attempts == 3 and len(calls) == 3 and "hunter2" not in d.last_error and d.last_error.startswith("RuntimeError")

    mode["fail"] = False
    async with session_factory() as s:
        assert await hub.retry(s, d.id) is True
    (d,) = await wait_until(lambda: _delivered(session_factory), what="delivery after retry")
    assert len(calls) == 4 and d.attempts == 1


async def test_retry_refuses_an_unknown_or_already_delivered_delivery(hub, session_factory):
    async def handler(e): ...

    hub.subscribe("job.complete", handler, name="t.retry")
    await publish(hub, session_factory, complete(1))
    (d,) = await wait_until(lambda: _delivered(session_factory), what="delivery")
    async with session_factory() as s:
        assert await hub.retry(s, d.id) is False and await hub.retry(s, 9999) is False


async def test_a_failing_or_hanging_subscriber_does_not_delay_or_block_another(hub, session_factory):
    good = []

    async def hang(e):
        await asyncio.sleep(60)

    async def ok(e):
        good.append(e.id)

    hub.subscribe("job.complete", hang, name="t.hang", timeout=30)
    hub.subscribe("job.complete", ok, name="t.ok")
    await publish(hub, session_factory, complete(1))
    await publish(hub, session_factory, complete(2))
    await wait_until(lambda: len(good) == 2, what="the healthy subscriber to finish both")

    async def by_subscriber():
        out = {}
        for d in await deliveries(session_factory):
            out.setdefault(d.subscriber, []).append(d.status)
        return out if out.get("core:t.ok") == ["delivered", "delivered"] else None

    by_sub = await wait_until(by_subscriber, what="both healthy deliveries acknowledged")
    assert by_sub["core:t.hang"][0] == "pending"


async def test_deliveries_to_one_subscriber_keep_commit_order_on_first_attempts(hub, session_factory):
    seen = []

    async def handler(e):
        seen.append(e.entities["job_id"])

    hub.subscribe("job.complete", handler, name="t.order")
    for n in range(1, 11):
        await publish(hub, session_factory, complete(n))
    await wait_until(lambda: len(seen) == 10, what="all deliveries")
    assert seen == list(range(1, 11))


async def test_a_plugin_subscriber_receives_a_durable_event_and_a_disabled_one_waits_then_catches_up(hub, session_factory):
    register(subscriber_manifest("sub_dur", "job.complete"))
    inst = await enable(plugin_host, "sub_dur")
    await publish(hub, session_factory, complete(1))
    await wait_until(lambda: inst.got, what="plugin delivery")

    await plugin_host.update_config("sub_dur", enabled=False)
    await publish(hub, session_factory, complete(2))             # the plugin's row is created only while it is a subscriber:
    assert [d.subscriber for d in await deliveries(session_factory)] == ["plugin:sub_dur:on_event"]   # complete(2) has none

    # a delivery created while enabled, then disabled before it ran, stays pending (dormant, no attempt used) and resumes
    inst2 = await enable(plugin_host, "sub_dur")
    plugin_host._configs["sub_dur"].enabled = False              # flip synchronously: the row is created, then the plugin goes dormant
    async with session_factory() as s:
        plugin_host._configs["sub_dur"].enabled = True
        await hub.enqueue_durable(s, complete(3))
        plugin_host._configs["sub_dur"].enabled = False
        await s.commit()
    hub.wake()
    await asyncio.sleep(0.2)
    pending = [d for d in await deliveries(session_factory) if d.status == "pending"]
    assert len(pending) == 1 and pending[0].attempts == 0 and inst2.got == []

    plugin_host._configs["sub_dur"].enabled = True
    await wait_until(lambda: [e.entities["job_id"] for e in inst2.got] == [3], what="catch-up after re-enable")


async def test_a_failing_plugin_handler_is_retried_and_its_error_is_redacted(hub, session_factory):
    register(subscriber_manifest("sub_err", "job.complete"))
    await enable(plugin_host, "sub_err", mode="raise", token="tok-TOP-SECRET")
    await publish(hub, session_factory, complete(1))

    async def retried():
        return [d for d in await deliveries(session_factory) if d.attempts >= 2] or None

    (d,) = await wait_until(retried, what="a retry")
    assert d.status == "pending" and "tok-TOP-SECRET" not in d.last_error and "hunter2" not in d.last_error


async def test_an_attempt_is_counted_before_the_handler_runs_so_a_crash_cannot_loop_forever(hub, session_factory, monkeypatch):
    monkeypatch.setattr(hubmod, "MAX_ATTEMPTS", 2)
    started = asyncio.Event()

    async def crashes(e):
        started.set()
        await asyncio.sleep(60)                                  # stands in for a process that dies mid-handler

    hub.subscribe("job.complete", crashes, name="t.crash", timeout=30)
    await publish(hub, session_factory, complete(1))
    await started.wait()
    (d,) = await deliveries(session_factory)
    assert d.status == "pending" and d.attempts == 1             # recorded even though the handler never finished


async def test_publish_and_enqueue_refuse_the_wrong_durability_path(hub, session_factory):
    with pytest.raises(EventError, match="durable"):
        await hub.publish(complete(1))
    async with session_factory() as s:
        with pytest.raises(EventError, match="best_effort"):
            await hub.enqueue_durable(s, EventEnvelope(name="job.blocked"))


async def test_a_plugin_defined_durable_event_is_enqueued_by_its_owner_only(hub, session_factory):
    register(definer_manifest("pub_dur", durability="durable"), subscriber_manifest("sub_ns", "pub_dur.ready"))
    await enable(plugin_host, "pub_dur")
    inst = await enable(plugin_host, "sub_ns")
    ev = EventEnvelope(name="pub_dur.ready", source="pub_dur", payload={"item": "x"}, dedup_key="pub_dur.ready:x")
    async with session_factory() as s:
        with pytest.raises(EventError, match="may not publish"):
            await hub.enqueue_durable(s, ev)                    # as core
        assert await hub.enqueue_durable(s, ev, as_plugin="pub_dur") is True
        await s.commit()
    hub.wake()
    await wait_until(lambda: inst.got, what="namespaced durable delivery")
    assert inst.got[0].id == ev.id
