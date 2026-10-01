"""BIZ-171 low-inventory alerts: thresholds, once-per-drop semantics, delivery, sync integration, config API."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.models import NotificationConfig, SpoolmanConfig, WebhookConfig
from app.services import spool_alerts
from app.services.spool_alerts import EVENT, find_low, threshold_for
from app.services.spoolman_sync import record_sync


def spool(sid, remaining, filament_id=1, location=None, vendor="Elegoo", archived=False):
    return {"id": sid, "remaining_weight": remaining, "archived": archived, "location": location,
            "filament": {"id": filament_id, "name": f"PLA {filament_id}", "vendor": {"name": vendor}}}


# ---- thresholds ------------------------------------------------------------------------------

@pytest.mark.parametrize("filament_id, default, overrides, expected", [
    (1, 200, None, 200.0),
    (1, None, None, None),
    (1, 200, {"1": 50}, 50.0),            # override wins, even when lower than the default
    (2, 200, {"1": 50}, 200.0),           # other filaments fall back to the default
    (2, None, {"1": 50}, None),           # an override only applies to its filament
    (1, 200, {"1": 0}, 0.0),              # 0 is a real threshold ("only alert when empty"), not "unset"
    (None, 100, {"1": 50}, 100.0),
])
def test_threshold_for(filament_id, default, overrides, expected):
    assert threshold_for(filament_id, default, overrides) == expected


def test_find_low_is_strictly_below_threshold_and_skips_archived_and_unweighed_spools():
    spools = [spool(1, 199.9), spool(2, 200), spool(3, 10, archived=True), {"id": 4, "filament": {"id": 1}},
              spool(5, 5, filament_id=9, location=" Shelf B ")]
    low = find_low(spools, 200, {"9": 10})

    assert [(l.spool_id, l.threshold_g, l.location) for l in low] == [(1, 200.0, None), (5, 10.0, "Shelf B")]
    assert find_low(spools, None, None) == []        # no thresholds → no alerts


# ---- once per drop ---------------------------------------------------------------------------

async def _row(session_factory, **fields):
    async with session_factory() as s:
        s.add(SpoolmanConfig(id=1, enabled=True, url="http://sm.test", **fields))
        await s.commit()


async def _process(session_factory, spools):
    async with session_factory() as s:
        row = await s.get(SpoolmanConfig, 1)
        fresh = await spool_alerts.process(s, row, spools)
        await s.commit()
    async with session_factory() as s:
        return fresh, (await s.get(SpoolmanConfig, 1)).low_stock_alerted


async def test_a_spool_alerts_once_per_drop_and_is_re_armed_after_a_refill(session_factory):
    await _row(session_factory, low_stock_default_g=100)

    fresh, alerted = await _process(session_factory, [spool(1, 80), spool(2, 500)])
    assert [l.spool_id for l in fresh] == [1] and alerted == [1]

    fresh, alerted = await _process(session_factory, [spool(1, 60), spool(2, 500)])   # still low: no repeat
    assert fresh == [] and alerted == [1]

    fresh, alerted = await _process(session_factory, [spool(1, 900), spool(2, 500)])  # refilled/replaced
    assert fresh == [] and alerted == []

    fresh, alerted = await _process(session_factory, [spool(1, 40), spool(2, 500)])   # drops again → alerts again
    assert [l.spool_id for l in fresh] == [1] and alerted == [1]


async def test_raising_a_threshold_alerts_newly_low_spools_and_other_spools_are_independent(session_factory):
    await _row(session_factory, low_stock_default_g=100)
    await _process(session_factory, [spool(1, 80), spool(2, 150)])

    async with session_factory() as s:
        (await s.get(SpoolmanConfig, 1)).low_stock_default_g = 200
        await s.commit()
    fresh, alerted = await _process(session_factory, [spool(1, 80), spool(2, 150)])

    assert [l.spool_id for l in fresh] == [2] and alerted == [1, 2]


async def test_no_thresholds_means_no_alerts_and_no_state(session_factory):
    await _row(session_factory)
    fresh, alerted = await _process(session_factory, [spool(1, 0)])
    assert fresh == [] and (alerted or []) == []


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
                     "threshold_g": 100.0, "location": "Shelf B"}
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
    assert [l.spool_id for l in fresh] == [2] and alerted == [2]     # 2 was delivered; 1 stays un-alerted

    with patch.object(spool_alerts.webhook_service, "schedule") as schedule:
        fresh, alerted = await _process(session_factory, [spool(1, 80), spool(2, 70)])
    assert [l.spool_id for l in fresh] == [1] and alerted == [1, 2]  # 1 retried (and only 1)
    assert [c.args[4]["spool_id"] for c in schedule.call_args_list] == [1]


# ---- sync integration ------------------------------------------------------------------------

async def test_record_sync_raises_alerts_for_the_spools_it_just_fetched_and_only_once(session_factory, spoolman_upstream):
    from tests import spoolman_mock
    await _row(session_factory, low_stock_default_g=900)   # every mock spool is below this
    async with session_factory() as s:
        s.add(WebhookConfig(id=1, url="http://hook.test", secret=None, events=[]))
        await s.commit()
    expected = sorted(sp["id"] for sp in spoolman_mock._SPOOLS if sp["remaining_weight"] < 900)
    assert expected   # the fixture really has low spools

    with patch.object(spool_alerts.webhook_service, "schedule") as schedule:
        for _ in range(2):   # a second sync must not repeat the alerts
            async with session_factory() as s:
                await record_sync(s, await s.get(SpoolmanConfig, 1))

    assert sorted(c.args[4]["spool_id"] for c in schedule.call_args_list) == expected
    async with session_factory() as s:
        assert (await s.get(SpoolmanConfig, 1)).low_stock_alerted == expected


async def test_a_failure_while_alerting_does_not_fail_or_roll_back_the_sync(session_factory, spoolman_upstream):
    await _row(session_factory, low_stock_default_g=900)
    with patch.object(spool_alerts, "process", side_effect=RuntimeError("config load failed")):
        async with session_factory() as s:
            counts = await record_sync(s, await s.get(SpoolmanConfig, 1))

    assert counts["spool_count"] == 2
    async with session_factory() as s:
        row = await s.get(SpoolmanConfig, 1)
    assert row.last_sync_at is not None and row.last_sync_error is None   # the sync is still recorded as successful
