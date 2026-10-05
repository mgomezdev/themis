"""Offline behaviour of REMOTE inventory providers (BIZ-219): last-known cache (stale reads, survives a restart), effective
remaining (pending writes overlaid), recovery flush, and the disconnect / reconnect alerts."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models import InventoryPendingWrite, WebhookConfig
from app.plugins.host import plugin_host
from app.plugins.kinds.filament_inventory import REMOTE, TRACKS_WEIGHT, WRITE_WEIGHT, InventoryProviderError
from app.services.inventory import cache, outbox, read, snapshots, sync, tasks
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import spool, use_provider

BASE = "/api/v1/inventory"
REMOTE_CAPS = frozenset({REMOTE, TRACKS_WEIGHT, WRITE_WEIGHT})
DOWN = InventoryProviderError("connection refused", code="ConnectError")


def remote(**kw) -> FakeInventoryProvider:
    return FakeInventoryProvider(spools=[spool("1", 500.0, name="Red"), spool("2", 80.0, name="Blue")],
                                 capabilities=REMOTE_CAPS, **kw)


async def _pending(factory, ref="1", target=400.0):
    async with factory() as s:
        outbox.enqueue(s, "spoolman", ref, target, job_id=None, printer_id=None, source="queue")
        await s.commit()


# ---- last-known cache ------------------------------------------------------------------------------------------------

async def test_a_live_read_is_cached_and_an_outage_serves_it_stale_with_its_age(client, session_factory):
    fake = remote()
    await use_provider(fake)
    live = (await client.get(f"{BASE}/spools")).json()
    assert live["stale"] is False and [i["ref"] for i in live["items"]] == ["1", "2"]

    fake.fail_with = DOWN
    down = (await client.get(f"{BASE}/spools")).json()

    assert down["stale"] is True and down["as_of"] != live["as_of"]
    assert [(i["ref"], i["remaining_g"], i["material"]["name"]) for i in down["items"]] == [("1", 500.0, "Red"), ("2", 80.0, "Blue")]


async def test_materials_are_cached_too_and_without_any_cache_an_outage_is_a_503(client, session_factory):
    from app.plugins.kinds.filament_inventory import InvMaterial
    fake = FakeInventoryProvider(materials=[InvMaterial(ref="9", name="PETG")], capabilities=REMOTE_CAPS)
    await use_provider(fake)
    fake.fail_with = DOWN
    assert (await client.get(f"{BASE}/materials")).status_code == 503                # nothing cached yet
    fake.fail_with = None
    assert (await client.get(f"{BASE}/materials")).json()["stale"] is False
    fake.fail_with = DOWN
    stale = (await client.get(f"{BASE}/materials")).json()
    assert stale["stale"] is True and [m["name"] for m in stale["items"]] == ["PETG"]


async def test_a_provider_that_is_not_remote_is_never_cached(client, session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 5.0)])                           # TRACKS + WRITE, not REMOTE
    await use_provider(fake)
    assert (await client.get(f"{BASE}/spools")).status_code == 200
    assert await cache.load("spoolman", "spools") is None
    fake.fail_with = DOWN
    assert (await client.get(f"{BASE}/spools")).status_code == 503


async def test_the_cache_is_per_provider_and_survives_a_restart(client, session_factory):
    fake = remote()
    await use_provider(fake, plugin_id="spoolman")
    await client.get(f"{BASE}/spools")

    plugin_host._reset()                                  # a restart: nothing in memory, the DB is all that is left
    plugin_host.configure(session_factory)
    other = remote()
    other.fail_with = DOWN
    await use_provider(other, plugin_id="other_remote")                              # another provider: must not see spoolman's cache
    assert (await client.get(f"{BASE}/spools")).status_code == 503

    again = remote()
    again.fail_with = DOWN
    await use_provider(again, plugin_id="spoolman")                                  # back to the first one, still down
    served = (await client.get(f"{BASE}/spools")).json()
    assert served["stale"] is True and len(served["items"]) == 2


# ---- effective remaining ---------------------------------------------------------------------------------------------

async def test_effective_remaining_overlays_the_newest_pending_write_and_marks_it_unsynced(client, session_factory):
    fake = remote()
    await use_provider(fake)
    await _pending(session_factory, "1", 480.0)
    await _pending(session_factory, "1", 460.0)

    items = {i["ref"]: i for i in (await client.get(f"{BASE}/spools")).json()["items"]}

    assert (items["1"]["remaining_g"], items["1"]["unsynced"]) == (460.0, True)
    assert (items["2"]["remaining_g"], items["2"]["unsynced"]) == (80.0, False)
    by_ref = await read.spools_by_ref("test")                                        # what preflight / low-stock use
    assert by_ref["1"].remaining_g == 460.0 and by_ref["2"].remaining_g == 80.0


async def test_preflight_style_reads_use_the_cache_during_an_outage(session_factory):
    fake = remote()
    await use_provider(fake)
    await read.spools_by_ref()                                                       # warms the cache
    fake.fail_with = DOWN
    assert (await read.spools_by_ref())["2"].remaining_g == 80.0


# ---- snapshots from the cache ----------------------------------------------------------------------------------------

async def test_the_snapshot_falls_back_to_the_cached_weight_only_when_the_provider_is_unreachable(session_factory):
    fake = remote()
    await use_provider(fake)
    await read.spools_reading()
    fake.fail_with = DOWN
    assert await snapshots.read_pre_weight(session_factory, "spoolman", "1") == (500.0, "cached")
    fake.fail_with = None
    assert await snapshots.read_pre_weight(session_factory, "spoolman", "999") == (None, "missing")   # reachable: truly unknown
    fake.fail_with = DOWN
    assert await snapshots.read_pre_weight(session_factory, "spoolman", "999") == (None, "missing")  # not in the cache either


# ---- recovery --------------------------------------------------------------------------------------------------------

async def test_recovery_flushes_pending_writes_in_order_and_the_cache_reflects_them(client, session_factory):
    fake = remote()
    await use_provider(fake)
    async with session_factory() as s:
        await sync.record_sync(s)                                                    # caches 500 g
    fake.fail_with = DOWN
    await _pending(session_factory, "1", 480.0)
    await _pending(session_factory, "1", 460.0)
    assert await outbox.flush(session_factory) == 0 and fake.writes == []

    fake.fail_with = None
    async with session_factory() as s:
        await sync.record_sync(s)                                                    # the provider is back

    assert fake.writes == [("1", 460.0)]
    assert (await cache.cached_weight("spoolman", "1")) == 460.0
    items = {i["ref"]: i for i in (await client.get(f"{BASE}/spools")).json()["items"]}
    assert (items["1"]["remaining_g"], items["1"]["unsynced"]) == (460.0, False)


# ---- disconnect alerts -----------------------------------------------------------------------------------------------

async def _alerting(factory, minutes=10):
    fake = remote()
    await use_provider(fake)
    await plugin_host.update_config("spoolman", settings={"max_disconnect_minutes": minutes})
    async with factory() as s:
        s.add(WebhookConfig(id=1, url="http://hook.test", secret=None, events=[]))
        await s.commit()
    return fake


async def _failing_sync(factory):
    async with factory() as s:
        with pytest.raises(InventoryProviderError):
            await sync.record_sync(s)


def _since(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


async def test_the_first_failure_starts_the_outage_clock_and_nothing_alerts_before_the_limit(session_factory):
    fake = await _alerting(session_factory)
    fake.fail_with = DOWN
    with patch("app.services.webhook_service.schedule") as hook:
        await _failing_sync(session_factory)
        await _failing_sync(session_factory)
    st = sync.status("spoolman")
    assert st["disconnected_since"] is not None and st["disconnect_alerted"] is False and st["max_disconnect_minutes"] == 10
    hook.assert_not_called()


async def test_exceeding_the_limit_alerts_exactly_once_per_outage_then_reconnect_reports_the_flush(session_factory):
    fake = await _alerting(session_factory)
    fake.fail_with = DOWN
    await _pending(session_factory, "1", 450.0)
    await plugin_host.record_state("spoolman", disconnected_since=_since(30))

    with patch("app.services.webhook_service.schedule") as hook:
        await _failing_sync(session_factory)
        await _failing_sync(session_factory)                                          # same outage: no second alert
        down = [(c.args[2], c.args[4]) for c in hook.call_args_list]
        assert [e for e, _ in down] == ["inventory.disconnected"]
        assert down[0][1]["provider"] == "spoolman" and down[0][1]["pending_count"] == 1
        assert sync.status("spoolman")["disconnect_alerted"] is True

        fake.fail_with = None
        async with session_factory() as s:
            await sync.record_sync(s)
        await tasks.drain()

    events = [(c.args[2], c.args[4]) for c in hook.call_args_list]
    assert [e for e, _ in events] == ["inventory.disconnected", "inventory.reconnected"]
    assert events[1][1]["flushed"] == 1 and events[1][1]["pending_count"] == 0 and events[1][1]["since"] == down[0][1]["since"]
    st = sync.status("spoolman")
    assert (st["disconnected_since"], st["disconnect_alerted"]) == (None, False)      # the outage is over
    assert fake.writes == [("1", 450.0)]


async def test_a_blank_limit_never_alerts_and_a_short_outage_recovers_silently(session_factory):
    fake = await _alerting(session_factory, minutes=None)
    fake.fail_with = DOWN
    await plugin_host.record_state("spoolman", disconnected_since=_since(600))
    with patch("app.services.webhook_service.schedule") as hook:
        await _failing_sync(session_factory)
        fake.fail_with = None
        async with session_factory() as s:
            await sync.record_sync(s)
    hook.assert_not_called()                                                          # no alert, so no "reconnected" either
    assert sync.status("spoolman")["disconnected_since"] is None


async def test_while_disconnected_the_loop_probes_every_poll_instead_of_waiting_for_the_interval(session_factory):
    fake = await _alerting(session_factory)
    loop = sync.InventorySyncLoop()
    loop.configure(session_factory)
    await loop._tick()                                                                # healthy: syncs, interval starts
    calls = fake.calls.count("list_materials")
    await loop._tick()
    assert fake.calls.count("list_materials") == calls                               # not due yet

    fake.fail_with = DOWN
    await plugin_host.record_state("spoolman", disconnected_since=_since(1))
    await loop._tick()
    assert fake.calls.count("list_materials") == calls + 1                           # probed despite the interval
