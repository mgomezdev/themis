"""Generic inventory sync (record_sync + the periodic loop + health) against the Spoolman plugin and a fake upstream."""
import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.plugins.host import plugin_host
from app.plugins.kinds.filament_inventory import REMOTE, TRACKS_WEIGHT, InventoryProviderError
from app.services.inventory import provider as inventory_provider, sync as inventory_sync
from app.services.inventory.sync import InventorySyncLoop, record_sync, status
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import enable_spoolman, use_provider

URL = "http://spoolman.test"


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"{URL}/api/v1/spool")
    return httpx.HTTPStatusError(f"Server error '{status}' for url", request=request,
                                 response=httpx.Response(status, request=request))


# --- failure description -------------------------------------------------------------------------------------------

async def test_describe_failure_uses_the_provider_error_code_or_the_exception_type(session_factory):
    fake = FakeInventoryProvider()
    await use_provider(fake)
    for exc, expected in [(InventoryProviderError("Server error", code="502", status=502), ("502", "Server error")),
                          (InventoryProviderError("refused", code="ConnectError"), ("ConnectError", "refused")),
                          (ValueError("bad json"), ("ValueError", "bad json"))]:
        fake.fail_with = exc
        assert inventory_provider.describe_failure(await inventory_provider.call("list_spools")) == expected


# --- record_sync ---------------------------------------------------------------------------------------------------------

async def test_record_sync_success_stamps_both_times_and_clears_a_previous_error(session_factory, spoolman_upstream):
    await enable_spoolman()
    await plugin_host.record_state("spoolman", sync_error="old failure", sync_error_code="500")
    async with session_factory() as s:
        counts = await record_sync(s)

    assert counts == {"material_count": 2, "spool_count": 2}
    st = status("spoolman")
    assert st["last_sync_at"] is not None and st["last_sync_at"] == st["last_attempt_at"]
    assert (st["last_error"], st["last_error_code"]) == (None, None)


async def test_record_sync_failure_records_the_error_keeps_the_last_success_and_reraises(session_factory, spoolman_upstream):
    await enable_spoolman()
    await plugin_host.record_state("spoolman", last_sync_at="2026-01-01T00:00:00+00:00")
    spoolman_upstream.handler = lambda request: httpx.Response(500, text="boom")

    async with session_factory() as s:
        with pytest.raises(InventoryProviderError) as e:
            await record_sync(s)

    st = status("spoolman")
    assert e.value.code == "500"
    assert st["last_sync_at"] == "2026-01-01T00:00:00+00:00"            # last SUCCESS untouched
    assert st["last_attempt_at"] is not None and st["last_attempt_at"] > st["last_sync_at"]
    assert st["last_error_code"] == "500" and "500" in st["last_error"]


async def test_record_sync_without_a_provider_is_a_neutral_error(session_factory):
    async with session_factory() as s:
        with pytest.raises(InventoryProviderError) as e:
            await record_sync(s)
    assert e.value.code == "NotConfigured"


async def test_the_sync_error_never_contains_the_api_key(session_factory, spoolman_upstream):
    await enable_spoolman(api_key="hunter2-key")
    spoolman_upstream.handler = lambda request: httpx.Response(500, text="boom hunter2-key boom")
    async with session_factory() as s:
        with pytest.raises(InventoryProviderError):
            await record_sync(s)
    assert "hunter2-key" not in str(status("spoolman"))


def test_status_defaults_when_nothing_is_configured():
    assert status() == {"enabled": False, "interval_minutes": 15, "last_sync_at": None, "last_attempt_at": None,
                        "last_error": None, "last_error_code": None}


# --- the periodic loop ----------------------------------------------------------------------------------------------------

def _loop_for(session_factory) -> InventorySyncLoop:
    loop = InventorySyncLoop()
    loop.configure(session_factory)
    return loop


@pytest.fixture
def fetched():
    """Count sync attempts without touching the network."""
    with patch("app.plugins.spoolman.client.fetch_filaments", new=AsyncMock(return_value=[])) as f, \
         patch("app.plugins.spoolman.client.fetch_spools", new=AsyncMock(return_value=[])):
        yield f


async def test_tick_does_nothing_with_no_provider_a_disabled_one_or_a_non_remote_one(session_factory, fetched):
    await _loop_for(session_factory)._tick()                                  # nothing configured
    await plugin_host.update_config("spoolman", settings={"url": URL}, enabled=False)
    await plugin_host.set_slot("filament_inventory", "spoolman")
    await plugin_host.update_config("spoolman", enabled=False)                # selected but disabled
    await _loop_for(session_factory)._tick()
    local = FakeInventoryProvider(capabilities=frozenset({TRACKS_WEIGHT}))   # active but not REMOTE: nothing to sync
    await use_provider(local)
    await _loop_for(session_factory)._tick()
    fetched.assert_not_called()
    assert local.calls == []


async def test_tick_does_nothing_when_not_due(session_factory, fetched):
    await enable_spoolman(sync_interval_minutes=15)
    await plugin_host.record_state("spoolman", last_attempt_at="9999-01-01T00:00:00+00:00")       # attempted "just now"
    await _loop_for(session_factory)._tick()
    fetched.assert_not_called()


@pytest.mark.parametrize("last_attempt_at", [None, "2000-01-01T00:00:00+00:00"])      # never tried / long ago
async def test_tick_syncs_when_due_and_records_the_attempt(session_factory, fetched, last_attempt_at):
    await enable_spoolman(sync_interval_minutes=15)
    if last_attempt_at:
        await plugin_host.record_state("spoolman", last_attempt_at=last_attempt_at)

    await _loop_for(session_factory)._tick()

    assert fetched.call_count == 1
    st = status("spoolman")
    assert st["last_sync_at"] is not None and st["last_attempt_at"] != last_attempt_at


async def test_tick_swallows_a_failed_sync_after_it_was_recorded(session_factory):
    await enable_spoolman(sync_interval_minutes=1)
    with patch("app.plugins.spoolman.client.fetch_filaments", new=AsyncMock(side_effect=_http_error(500))):
        await _loop_for(session_factory)._tick()  # must not raise
    st = status("spoolman")
    assert st["last_error_code"] == "500" and st["last_sync_at"] is None


async def test_tick_treats_a_zero_minute_interval_as_one_minute(session_factory, fetched):
    from datetime import datetime, timedelta, timezone
    await enable_spoolman()
    await plugin_host.update_config("spoolman", settings={"sync_interval_minutes": 1})
    await plugin_host.record_state("spoolman", last_attempt_at=(datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat())
    await _loop_for(session_factory)._tick()
    fetched.assert_not_called()  # 30s < the 60s floor


async def test_start_is_idempotent_and_stop_cancels_the_loop(session_factory):
    loop = _loop_for(session_factory)
    with patch.object(loop, "_tick", new=AsyncMock()):
        await loop.start()
        first = loop._task
        await loop.start()
        assert loop._task is first and first.get_name() == "inventory_sync_loop"
        await loop.stop()

    assert first.cancelled() or first.done()
    assert loop._task is None
    await loop.stop()  # stopping a stopped loop is a no-op


async def test_loop_survives_a_failing_tick_polls_every_minute_and_propagates_cancellation(session_factory):
    loop = _loop_for(session_factory)
    ticks, sleeps = [], []

    async def flaky_tick():
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("tick blew up")

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    with patch.object(loop, "_tick", flaky_tick), patch.object(inventory_sync.asyncio, "sleep", fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await loop._loop()

    assert len(ticks) == 2  # the first tick's exception did not stop the loop
    assert sleeps == [60, 60]
