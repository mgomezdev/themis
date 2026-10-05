"""BIZ-171 low-inventory alerts: thresholds, once-per-drop semantics, delivery, sync integration, config API."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.models import InventoryConfig, NotificationConfig, WebhookConfig
from app.plugins.host import plugin_host
from app.plugins.kinds.filament_inventory import InvMaterial, InvSpool
from app.services.inventory import alerts as spool_alerts
from app.services.inventory.alerts import EVENT, find_low, threshold_for
from app.services.inventory.sync import record_sync
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import enable_spoolman, use_provider

P = "spoolman"      # the provider id these tests run as (alert state is namespaced by it)


def spool(sid, remaining, filament_id=1, location=None, vendor="Elegoo", archived=False):
    mat = InvMaterial(ref=str(filament_id), name=f"PLA {filament_id}", vendor=vendor)
    return InvSpool(ref=str(sid), material_ref=str(filament_id), material=mat, label="", location=location,
                    remaining_g=remaining, archived=archived)


# ---- thresholds ------------------------------------------------------------------------------

@pytest.mark.parametrize("filament_id, default, overrides, expected", [
    ("1", 200, None, 200.0),
    ("1", None, None, None),
    ("1", 200, {"1": 50}, 50.0),          # override wins, even when lower than the default
    ("2", 200, {"1": 50}, 200.0),         # other filaments fall back to the default
    ("2", None, {"1": 50}, None),         # an override only applies to its filament
    ("1", 200, {"1": 0}, 0.0),            # 0 is a real threshold ("only alert when empty"), not "unset"
    (None, 100, {"1": 50}, 100.0),
])
def test_threshold_for(filament_id, default, overrides, expected):
    assert threshold_for(filament_id, default, overrides) == expected


def test_find_low_is_strictly_below_threshold_and_skips_archived_and_unweighed_spools():
    spools = [spool(1, 199.9), spool(2, 200), spool(3, 10, archived=True), InvSpool(ref="4", material_ref="1"),
              spool(5, 5, filament_id=9, location=" Shelf B ")]
    low = find_low(spools, 200, {"9": 10})

    assert [(l.spool_id, l.threshold_g, l.location) for l in low] == [(1, 200.0, None), (5, 10.0, "Shelf B")]
    assert find_low(spools, None, None) == []        # no thresholds → no alerts


# ---- once per drop ---------------------------------------------------------------------------

async def _row(session_factory, **fields):
    async with session_factory() as s:
        s.add(InventoryConfig(id=1, deduct_on_complete=True, low_stock_overrides=fields.pop("low_stock_overrides", {}),
                              low_stock_alerted=fields.pop("low_stock_alerted", []), **fields))
        await s.commit()


async def _process(session_factory, spools, provider=P):
    async with session_factory() as s:
        fresh = await spool_alerts.process(s, provider, spools)
        await s.commit()
    async with session_factory() as s:
        return fresh, (await s.get(InventoryConfig, 1)).low_stock_alerted


def _ids(alerted):
    """The alerted spool refs of provider P (the stored keys are namespaced "<provider>:<ref>")."""
    return [int(k.split(":", 1)[1]) for k in (alerted or []) if k.startswith(f"{P}:")]


async def test_a_spool_alerts_once_per_drop_and_is_re_armed_after_a_refill(session_factory):
    await _row(session_factory, low_stock_default_g=100)

    fresh, alerted = await _process(session_factory, [spool(1, 80), spool(2, 500)])
    assert [l.spool_id for l in fresh] == [1] and _ids(alerted) == [1]

    fresh, alerted = await _process(session_factory, [spool(1, 60), spool(2, 500)])   # still low: no repeat
    assert fresh == [] and _ids(alerted) == [1]

    fresh, alerted = await _process(session_factory, [spool(1, 900), spool(2, 500)])  # refilled/replaced
    assert fresh == [] and _ids(alerted) == []

    fresh, alerted = await _process(session_factory, [spool(1, 40), spool(2, 500)])   # drops again → alerts again
    assert [l.spool_id for l in fresh] == [1] and _ids(alerted) == [1]


async def test_raising_a_threshold_alerts_newly_low_spools_and_other_spools_are_independent(session_factory):
    await _row(session_factory, low_stock_default_g=100)
    await _process(session_factory, [spool(1, 80), spool(2, 150)])

    async with session_factory() as s:
        (await s.get(InventoryConfig, 1)).low_stock_default_g = 200
        await s.commit()
    fresh, alerted = await _process(session_factory, [spool(1, 80), spool(2, 150)])

    assert [l.spool_id for l in fresh] == [2] and _ids(alerted) == [1, 2]


async def test_no_thresholds_means_no_alerts_and_no_state(session_factory):
    await _row(session_factory)
    fresh, alerted = await _process(session_factory, [spool(1, 0)])
    assert fresh == [] and _ids(alerted) == []


# ---- delivery --------------------------------------------------------------------------------

async def test_delivery_fires_the_webhook_and_notification_channels_for_spool_low(session_factory):
    await _row(session_factory, low_stock_default_g=100)
    async with session_factory() as s:
        s.add(WebhookConfig(id=1, url="http://hook.test", secret="s", events=[EVENT]))
        s.add(NotificationConfig(id=1, ntfy_enabled=True, ntfy_server_url="http://ntfy.test", ntfy_topic="t",
                                 ntfy_events=[EVENT], discord_events=[], email_to_addrs=[], email_events=[]))
        await s.commit()

    with patch.object(spool_alerts.webhook_service, "schedule") as schedule, \
         patch.object(spool_alerts.notification_service, "dispatch", new=AsyncMock()) as dispatch:
        await _process(session_factory, [spool(1, 80, location="Shelf B")])
        await asyncio.sleep(0)   # let the fire-and-forget dispatch task run

    (url, secret, event, job_id, extra), _ = schedule.call_args
    assert (url, secret, event, job_id) == ("http://hook.test", "s", "spool.low", None)
    assert extra == {"spool_id": 1, "filament_id": 1, "name": "Elegoo PLA 1", "remaining_g": 80.0,
                     "threshold_g": 100.0, "location": "Shelf B",
                     "provider": "spoolman", "spool_ref": "1", "material_ref": "1"}
    (_cfg, event, job_id, title, message), _ = dispatch.call_args
    assert (event, job_id, title) == ("spool.low", None, "Themis: spool running low")
    assert message == "Elegoo PLA 1 (Shelf B) has 80 g left (alert below 100 g)."


async def test_the_webhook_is_skipped_when_its_event_list_excludes_spool_low(session_factory):
    await _row(session_factory, low_stock_default_g=100)
    async with session_factory() as s:
        s.add(WebhookConfig(id=1, url="http://hook.test", secret=None, events=["job.failed"]))
        await s.commit()
    with patch.object(spool_alerts.webhook_service, "schedule") as schedule:
        await _process(session_factory, [spool(1, 80)])
    schedule.assert_not_called()


async def test_a_failed_delivery_is_retried_at_the_next_sync_and_does_not_block_other_spools(session_factory):
    await _row(session_factory, low_stock_default_g=100)
    async with session_factory() as s:
        s.add(WebhookConfig(id=1, url="http://hook.test", secret=None, events=[]))
        await s.commit()

    def flaky(url, secret, event, job_id, extra):
        if extra["spool_id"] == 1:
            raise RuntimeError("boom")
    with patch.object(spool_alerts.webhook_service, "schedule", side_effect=flaky):
        fresh, alerted = await _process(session_factory, [spool(1, 80), spool(2, 70)])
    assert [l.spool_id for l in fresh] == [2] and _ids(alerted) == [2]     # 2 was delivered; 1 stays un-alerted

    with patch.object(spool_alerts.webhook_service, "schedule") as schedule:
        fresh, alerted = await _process(session_factory, [spool(1, 80), spool(2, 70)])
    assert [l.spool_id for l in fresh] == [1] and _ids(alerted) == [1, 2]  # 1 retried (and only 1)
    assert [c.args[4]["spool_id"] for c in schedule.call_args_list] == [1]


# ---- sync integration ------------------------------------------------------------------------

async def test_record_sync_raises_alerts_for_the_spools_it_just_fetched_and_only_once(session_factory, spoolman_upstream):
    from tests import spoolman_mock
    await enable_spoolman()
    await _row(session_factory, low_stock_default_g=900)   # every mock spool is below this
    async with session_factory() as s:
        s.add(WebhookConfig(id=1, url="http://hook.test", secret=None, events=[]))
        await s.commit()
    expected = sorted(sp["id"] for sp in spoolman_mock._SPOOLS if sp["remaining_weight"] < 900)
    assert expected   # the fixture really has low spools

    with patch.object(spool_alerts.webhook_service, "schedule") as schedule:
        for _ in range(2):   # a second sync must not repeat the alerts
            async with session_factory() as s:
                await record_sync(s)

    assert sorted(c.args[4]["spool_id"] for c in schedule.call_args_list) == expected
    async with session_factory() as s:
        assert _ids((await s.get(InventoryConfig, 1)).low_stock_alerted) == expected


async def test_a_failure_while_alerting_does_not_fail_or_roll_back_the_sync(session_factory, spoolman_upstream):
    await enable_spoolman()
    await _row(session_factory, low_stock_default_g=900)
    with patch.object(spool_alerts, "process", side_effect=RuntimeError("config load failed")):
        async with session_factory() as s:
            counts = await record_sync(s)

    assert counts["spool_count"] == 2
    state = plugin_host.state("spoolman")
    assert state["last_sync_at"] is not None and state["sync_error"] is None   # the sync is still recorded as successful


# ---- provider namespacing + capability gating (BIZ-215) ---------------------------------------

async def test_thresholds_and_alert_state_are_scoped_to_the_provider(session_factory):
    await _row(session_factory, low_stock_default_g=100, low_stock_overrides={"other:1": 5.0, "spoolman:1": 999.0},
               low_stock_alerted=["other:7"])
    fresh, alerted = await _process(session_factory, [spool(1, 500)])          # spoolman:1's override (999) applies

    assert [l.spool_id for l in fresh] == [1]
    assert sorted(alerted) == ["other:7", "spoolman:1"]                       # the other provider's state is untouched

    fresh, alerted = await _process(session_factory, [spool(1, 500)], provider="other")   # other:1's override is 5 g
    # 500 g is not below other's 5 g override; spool 7 is gone from its list (re-armed); spoolman's key is untouched
    assert fresh == [] and sorted(alerted) == ["spoolman:1"]


async def test_a_provider_that_does_not_track_weight_never_raises_low_stock_alerts(session_factory):
    from app.plugins.kinds.filament_inventory import REMOTE
    await _row(session_factory, low_stock_default_g=900)
    fake = FakeInventoryProvider(spools=[spool(1, 10)], capabilities=frozenset({REMOTE}))     # no TRACKS_WEIGHT
    await use_provider(fake)
    async with session_factory() as s:
        s.add(WebhookConfig(id=1, url="http://hook.test", secret=None, events=[]))
        await s.commit()
    with patch.object(spool_alerts.webhook_service, "schedule") as schedule:
        async with session_factory() as s:
            await record_sync(s)
    schedule.assert_not_called()
