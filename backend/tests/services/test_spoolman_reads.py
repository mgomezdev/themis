"""spoolman_service read paths and spoolman_sync (record_sync, the periodic loop) against a fake upstream."""
import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.models import SpoolmanConfig
from app.services import spoolman_service, spoolman_sync
from app.services.spoolman_sync import SpoolmanSyncLoop, _describe_error, record_sync
from tests import spoolman_mock

URL = "http://spoolman.test"


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"{URL}/api/v1/spool")
    return httpx.HTTPStatusError(f"Server error '{status}' for url", request=request,
                                 response=httpx.Response(status, request=request))


# ---------------------------------------------------------------------------
# spoolman_service reads
# ---------------------------------------------------------------------------

async def test_reads_return_the_upstream_payloads(spoolman_upstream):
    assert await spoolman_service.fetch_filaments(URL) == spoolman_mock._FILAMENTS
    assert await spoolman_service.fetch_spools(URL) == spoolman_mock._SPOOLS
    assert await spoolman_service.fetch_filament(URL, None, 2) == spoolman_mock._FILAMENTS[1]
    assert await spoolman_service.test_connection(URL) == {"version": "1.0.0-mock", "debug_mode": False}
    assert [(r.method, r.url.path) for r in spoolman_upstream.requests] == [
        ("GET", "/api/v1/filament"), ("GET", "/api/v1/spool"), ("GET", "/api/v1/filament/2"), ("GET", "/api/v1/info")]


async def test_reads_strip_the_trailing_slash_and_send_the_api_key_only_when_set(spoolman_upstream):
    await spoolman_service.fetch_spools(f"{URL}/", "secret")
    await spoolman_service.fetch_spools(URL, None)

    with_key, without_key = spoolman_upstream.requests
    assert str(with_key.url) == f"{URL}/api/v1/spool"  # no '//api'
    assert with_key.headers["x-api-key"] == "secret"
    assert "x-api-key" not in without_key.headers


@pytest.mark.parametrize("call", [
    lambda: spoolman_service.fetch_filaments(URL),
    lambda: spoolman_service.fetch_spools(URL),
    lambda: spoolman_service.fetch_filament(URL, None, 1),
    lambda: spoolman_service.test_connection(URL),
])
async def test_reads_raise_on_http_errors_and_timeouts(spoolman_upstream, call):
    spoolman_upstream.handler = lambda request: httpx.Response(503, text="down")
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await call()
    assert exc.value.response.status_code == 503

    def _timeout(request):
        raise httpx.ConnectTimeout("timed out", request=request)
    spoolman_upstream.handler = _timeout
    with pytest.raises(httpx.ConnectTimeout):
        await call()


# ---------------------------------------------------------------------------
# _describe_error
# ---------------------------------------------------------------------------

def test_describe_error_classifies_status_transport_and_other_failures():
    status_code, status_msg = _describe_error(_http_error(502))
    assert status_code == "502" and "502" in status_msg

    transport = httpx.ConnectError("refused", request=httpx.Request("GET", URL))
    assert _describe_error(transport) == ("ConnectError", "refused")
    assert _describe_error(ValueError("bad json")) == ("ValueError", "bad json")


# ---------------------------------------------------------------------------
# record_sync
# ---------------------------------------------------------------------------

async def _row(session_factory, **fields) -> None:
    async with session_factory() as s:
        s.add(SpoolmanConfig(id=1, enabled=True, url=URL, **fields))
        await s.commit()


async def _reload(session_factory) -> SpoolmanConfig:
    async with session_factory() as s:
        return await s.get(SpoolmanConfig, 1)


async def test_record_sync_success_stamps_both_times_and_clears_a_previous_error(session_factory, spoolman_upstream):
    await _row(session_factory, last_sync_error="old failure", last_sync_error_code="500")
    async with session_factory() as s:
        counts = await record_sync(s, await s.get(SpoolmanConfig, 1))

    assert counts == {"filament_count": 2, "spool_count": 2}
    row = await _reload(session_factory)
    assert row.last_sync_at is not None and row.last_sync_at == row.last_attempt_at
    assert (row.last_sync_error, row.last_sync_error_code) == (None, None)


async def test_record_sync_failure_records_the_error_keeps_the_last_success_and_reraises(session_factory, spoolman_upstream):
    await _row(session_factory, last_sync_at="2026-01-01T00:00:00+00:00")
    spoolman_upstream.handler = lambda request: httpx.Response(500, text="boom")

    async with session_factory() as s:
        with pytest.raises(httpx.HTTPStatusError):
            await record_sync(s, await s.get(SpoolmanConfig, 1))

    row = await _reload(session_factory)
    assert row.last_sync_at == "2026-01-01T00:00:00+00:00"  # last SUCCESS untouched
    assert row.last_attempt_at is not None and row.last_attempt_at > row.last_sync_at
    assert row.last_sync_error_code == "500" and "500" in row.last_sync_error


# ---------------------------------------------------------------------------
# SpoolmanSyncLoop._tick / lifecycle / _loop
# ---------------------------------------------------------------------------

def _loop_for(session_factory) -> SpoolmanSyncLoop:
    loop = SpoolmanSyncLoop()
    loop.configure(session_factory)
    return loop


@pytest.fixture
def fetched():
    """Count sync attempts without touching the network."""
    with patch.object(spoolman_sync.spoolman_service, "fetch_filaments", new=AsyncMock(return_value=[])) as f, \
         patch.object(spoolman_sync.spoolman_service, "fetch_spools", new=AsyncMock(return_value=[])):
        yield f


@pytest.mark.parametrize("fields", [
    None,                                   # no config row at all
    {"enabled": False, "url": URL},         # disabled
    {"enabled": True, "url": ""},           # no URL
    {"enabled": True, "url": URL, "sync_interval_minutes": 15,
     "last_attempt_at": "9999-01-01T00:00:00+00:00"},  # attempted "just now" -> not due yet
])
async def test_tick_does_nothing_when_not_configured_or_not_due(session_factory, fetched, fields):
    if fields is not None:
        async with session_factory() as s:
            s.add(SpoolmanConfig(id=1, **fields))
            await s.commit()

    await _loop_for(session_factory)._tick()

    fetched.assert_not_called()


@pytest.mark.parametrize("last_attempt_at", [
    None,                              # never tried
    "2000-01-01T00:00:00+00:00",       # long ago
])
async def test_tick_syncs_when_due_and_records_the_attempt(session_factory, fetched, last_attempt_at):
    await _row(session_factory, sync_interval_minutes=15, last_attempt_at=last_attempt_at)

    await _loop_for(session_factory)._tick()

    assert fetched.call_count == 1
    row = await _reload(session_factory)
    assert row.last_sync_at is not None and row.last_attempt_at != last_attempt_at


async def test_tick_swallows_a_failed_sync_after_it_was_recorded_on_the_row(session_factory):
    await _row(session_factory, sync_interval_minutes=1)
    with patch.object(spoolman_sync.spoolman_service, "fetch_filaments",
                      new=AsyncMock(side_effect=_http_error(500))):
        await _loop_for(session_factory)._tick()  # must not raise

    row = await _reload(session_factory)
    assert row.last_sync_error_code == "500" and row.last_sync_at is None


async def test_tick_treats_a_zero_minute_interval_as_one_minute(session_factory, fetched):
    from datetime import datetime, timedelta, timezone
    recent = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    await _row(session_factory, sync_interval_minutes=0, last_attempt_at=recent)

    await _loop_for(session_factory)._tick()

    fetched.assert_not_called()  # 30s < the 60s floor


async def test_start_is_idempotent_and_stop_cancels_the_loop(session_factory):
    loop = _loop_for(session_factory)
    with patch.object(loop, "_tick", new=AsyncMock()):
        await loop.start()
        first = loop._task
        await loop.start()
        assert loop._task is first and first.get_name() == "spoolman_sync_loop"

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

    with patch.object(loop, "_tick", flaky_tick), patch.object(spoolman_sync.asyncio, "sleep", fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await loop._loop()

    assert len(ticks) == 2  # the first tick's exception did not stop the loop
    assert sleeps == [60, 60]
