"""Best-effort delivery (BIZ-249): per-subscriber lanes, containment, ordering, backpressure, duplicates, plugin lifecycle."""
import asyncio

import pytest

from app.eventing import EventEnvelope, EventError
from app.eventing.hub import EventHub
from app.plugins.host import plugin_host
from tests.eventing.fakes import definer_manifest, enable, register, subscriber_manifest
from tests.waiting import wait_until


def blocked(n: int = 1, **kw) -> EventEnvelope:
    return EventEnvelope(name="job.blocked", entities={"job_id": n}, dedup_key=f"job.blocked:{n}:{kw.pop('salt', '')}", **kw)


@pytest.fixture
async def hub(session_factory):
    h = EventHub()
    yield h
    await h.stop()


@pytest.fixture(autouse=True)
async def _host(session_factory):
    await plugin_host.start()
    yield


async def test_a_core_subscriber_receives_the_published_envelope(hub):
    got = []

    async def handler(e):
        got.append(e)

    hub.subscribe("job.blocked", handler, name="t.recorder")
    event = blocked(7)
    assert await hub.publish(event) is True
    await hub.drain()

    assert got == [event] and got[0].entities == {"job_id": 7}
    (row,) = [r for r in hub.snapshot() if r["subscriber"] == "core:t.recorder"]
    assert (row["delivered"], row["failed"], row["last_event_id"]) == (1, 0, event.id)


async def test_a_subscriber_only_receives_events_it_subscribed_to(hub):
    got = []

    async def handler(e):
        got.append(e.name)

    hub.subscribe("job.failed", handler, name="t.failed_only")
    await hub.publish(blocked(1))
    await hub.drain()
    assert got == []


async def test_a_failing_and_a_hanging_subscriber_never_stop_the_others_or_the_publisher(hub):
    good = []

    async def boom(e):
        raise RuntimeError("kaboom")

    async def hang(e):
        await asyncio.sleep(60)

    async def ok(e):
        good.append(e.id)

    hub.subscribe("job.blocked", boom, name="t.boom")
    hub.subscribe("job.blocked", hang, name="t.hang", timeout=0.05)
    hub.subscribe("job.blocked", ok, name="t.ok")
    ev = blocked(1)
    await asyncio.wait_for(hub.publish(ev), timeout=1)         # the publisher returns without waiting for anyone
    await hub.drain()

    assert good == [ev.id]
    rows = {r["subscriber"]: r for r in hub.snapshot()}
    assert rows["core:t.boom"]["failed"] == 1 and "kaboom" in rows["core:t.boom"]["last_error"]
    assert rows["core:t.hang"]["timed_out"] == 1 and "timed out" in rows["core:t.hang"]["last_error"]
    assert rows["core:t.ok"]["delivered"] == 1 and rows["core:t.ok"]["failed"] == 0


async def test_one_subscribers_failure_does_not_poison_its_later_events(hub):
    seen = []

    async def flaky(e):
        seen.append(e.entities["job_id"])
        if e.entities["job_id"] == 1:
            raise RuntimeError("first one fails")

    hub.subscribe("job.blocked", flaky, name="t.flaky")
    for n in (1, 2, 3):
        await hub.publish(blocked(n))
    await hub.drain()
    assert seen == [1, 2, 3]


async def test_events_reach_a_subscriber_in_publication_order_one_at_a_time(hub):
    seen, running, overlap = [], 0, 0

    async def handler(e):
        nonlocal running, overlap
        running += 1
        overlap = max(overlap, running)
        await asyncio.sleep(0)
        seen.append(e.entities["job_id"])
        running -= 1

    hub.subscribe("job.blocked", handler, name="t.ordered")
    for n in range(40):
        await hub.publish(blocked(n))
    await hub.drain()
    assert seen == list(range(40)) and overlap == 1


async def test_a_full_lane_drops_new_events_and_counts_them_without_blocking_the_publisher(hub):
    release, seen = asyncio.Event(), []

    async def slow(e):
        await release.wait()
        seen.append(e.entities["job_id"])

    hub.subscribe("job.blocked", slow, name="t.slow", queue_size=2)
    await hub.publish(blocked(0))
    await wait_until(lambda: hub._lanes["core:t.slow"].queue.empty(), what="first event picked up")     # now in flight
    for n in range(1, 8):
        await asyncio.wait_for(hub.publish(blocked(n)), timeout=1)
    (row,) = [r for r in hub.snapshot() if r["subscriber"] == "core:t.slow"]
    assert row["queue_depth"] == 2 and row["dropped"] == 5
    release.set()
    await hub.drain()
    assert seen == [0, 1, 2]                                    # the oldest were kept; nothing was reordered


async def test_publishing_the_same_logical_event_twice_delivers_it_once(hub):
    got = []

    async def handler(e):
        got.append(e.id)

    hub.subscribe("job.blocked", handler, name="t.once")
    first, again = blocked(5), blocked(5)                       # same dedup_key, different envelope ids
    assert await hub.publish(first) is True
    assert await hub.publish(again) is False
    await hub.drain()
    assert got == [first.id]


async def test_publish_rejects_a_malformed_or_misrouted_envelope_and_a_durable_event(hub):
    with pytest.raises(EventError, match="unknown event"):
        await hub.publish(EventEnvelope(name="job.nope"))
    with pytest.raises(EventError, match="durable"):
        await hub.publish(EventEnvelope(name="job.complete", payload={"source": "queue"}))


async def test_core_subscriber_registration_is_validated(hub):
    async def h(e): ...

    hub.subscribe("job.blocked", h, name="t.one")
    with pytest.raises(EventError, match="already registered"):
        hub.subscribe("job.blocked", h, name="t.one")
    with pytest.raises(EventError, match="not an event name"):
        hub.subscribe("NOPE", h, name="t.two")


# --- plugins ---------------------------------------------------------------------------------------------------

async def test_a_plugin_subscribes_to_a_core_event_and_another_defines_and_publishes_its_own(hub):
    register(subscriber_manifest("sub_core", "job.blocked"), subscriber_manifest("sub_ns", "pub_one.ready"), definer_manifest("pub_one"))
    core_sub = await enable(plugin_host, "sub_core")
    ns_sub = await enable(plugin_host, "sub_ns")
    await enable(plugin_host, "pub_one")

    await hub.publish(blocked(3))
    ready = EventEnvelope(name="pub_one.ready", source="pub_one", payload={"item": "gear"})
    await hub.publish(ready, as_plugin="pub_one")
    await hub.drain()

    assert [e.entities for e in core_sub.got] == [{"job_id": 3}]
    assert [e.id for e in ns_sub.got] == [ready.id] and ns_sub.got[0].payload == {"item": "gear"}
    assert core_sub.got[0].name == "job.blocked"                # each plugin only got what it subscribed to


async def test_a_failing_plugin_handler_is_contained_redacted_and_leaves_other_subscribers_alone(hub):
    register(subscriber_manifest("sub_bad", "job.blocked"), subscriber_manifest("sub_good", "job.blocked"))
    await enable(plugin_host, "sub_bad", mode="raise", token="tok-SECRET-1")
    good = await enable(plugin_host, "sub_good")

    await asyncio.wait_for(hub.publish(blocked(1)), timeout=1)
    await hub.drain()

    assert len(good.got) == 1
    rows = {r["subscriber"]: r for r in hub.snapshot()}
    bad = rows["plugin:sub_bad:on_event"]
    assert bad["failed"] == 1 and bad["plugin_id"] == "sub_bad"
    for leaked in ("tok-SECRET-1", "hunter2"):
        assert leaked not in bad["last_error"]
    assert rows["plugin:sub_good:on_event"]["delivered"] == 1
    assert "tok-SECRET-1" not in str(plugin_host.state("sub_bad"))           # nor in the plugin's persisted state


async def test_a_hanging_plugin_handler_times_out_without_blocking_anyone(hub):
    register(subscriber_manifest("sub_hang", "job.blocked", timeout=0.05), subscriber_manifest("sub_fast", "job.blocked"))
    await enable(plugin_host, "sub_hang", mode="hang")
    fast = await enable(plugin_host, "sub_fast")
    await hub.publish(blocked(1))
    await hub.drain()
    assert len(fast.got) == 1
    assert {r["subscriber"]: r for r in hub.snapshot()}["plugin:sub_hang:on_event"]["timed_out"] == 1


async def test_a_disabled_or_uninstalled_plugin_gets_nothing_and_resumes_when_enabled_again(hub):
    register(subscriber_manifest("sub_life", "job.blocked"))
    inst = await enable(plugin_host, "sub_life")
    await hub.publish(blocked(1))
    await hub.drain()
    assert len(inst.got) == 1

    await plugin_host.update_config("sub_life", enabled=False)
    assert hub.subscribers_for("job.blocked") == []
    await hub.publish(blocked(2))
    await hub.drain()
    assert len(inst.got) == 1                                   # the old instance saw nothing while disabled

    inst2 = await enable(plugin_host, "sub_life")
    await hub.publish(blocked(3))
    await hub.drain()
    assert [e.entities["job_id"] for e in inst2.got] == [3]     # no replay of what it missed

    from app import plugins
    plugins._REGISTRY.pop("sub_life")                           # uninstalled: the subscription is simply gone
    assert hub.subscribers_for("job.blocked") == []


async def test_an_event_queued_for_a_plugin_that_is_disabled_before_delivery_is_skipped_and_counted(hub):
    register(subscriber_manifest("sub_gone", "job.blocked"))
    await enable(plugin_host, "sub_gone")
    await hub.publish(blocked(1))                               # queued in the lane; publish() never yields, so it has not run
    plugin_host._configs["sub_gone"].enabled = False            # the snapshot flips synchronously, before the worker gets a turn
    await hub.drain()
    row = {r["subscriber"]: r for r in hub.snapshot()}["plugin:sub_gone:on_event"]
    assert row["delivered"] == 0 and row["skipped_inactive"] == 1 and row["failed"] == 0


async def test_a_subscription_to_an_event_whose_definer_is_gone_is_dormant_not_an_error(hub):
    register(subscriber_manifest("sub_opt", "pub_one.ready"))                      # pub_one is not installed
    inst = await enable(plugin_host, "sub_opt")
    assert hub.subscribers_for("pub_one.ready")[0].plugin_id == "sub_opt"
    assert inst.got == [] and plugin_host.build_error("sub_opt") is None


async def test_a_handler_that_is_not_async_is_never_called_and_is_reported(hub):
    register(subscriber_manifest("sub_sync", "job.blocked", handler="not_async"))
    inst = await enable(plugin_host, "sub_sync")
    await hub.publish(blocked(1))
    await hub.drain()
    assert inst.got == []
    row = {r["subscriber"]: r for r in hub.snapshot()}["plugin:sub_sync:not_async"]
    assert "not async" in row["last_error"]


async def test_publishing_a_plugin_event_as_the_wrong_plugin_is_refused(hub):
    register(definer_manifest("pub_one"), definer_manifest("pub_two"))
    await enable(plugin_host, "pub_one")
    with pytest.raises(EventError, match="may not publish"):
        await hub.publish(EventEnvelope(name="pub_one.ready", source="pub_one", payload={"item": "x"}), as_plugin="pub_two")


async def test_a_disabled_plugin_cannot_publish(hub):
    register(definer_manifest("pub_one"))
    with pytest.raises(EventError, match="not enabled"):
        await hub.publish(EventEnvelope(name="pub_one.ready", source="pub_one", payload={"item": "x"}), as_plugin="pub_one")
