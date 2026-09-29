"""GET /laminus/catalog, POST /laminus/catalog/rescan, get_cached_catalog and the startup warm-up."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import app.api.routes.laminus as lmod
from app.services.laminus_sidecar_client import SidecarError

def advancing_clock(step: float = 10.0):
    """A time.time() stand-in that moves `step` seconds per call, so a poll loop with a wall-clock deadline
    finishes in a few iterations instead of real minutes (and never exhausts: logging reads time.time() too)."""
    import time as _time
    state = {"now": _time.time()}

    def clock():
        state["now"] += step
        return state["now"]
    return clock


CATALOG = {"machine": [{"name": "M", "uuid": "m"}], "process": [{"name": "P", "uuid": "p"}], "filament": []}
URL = "http://laminus.test"


@pytest.fixture
def sidecar():
    """A configured sidecar whose get_catalog we control (`s.catalog` / `s.error`)."""
    from types import SimpleNamespace
    s = SimpleNamespace(catalog=CATALOG, error=None, calls=0)

    def get_catalog():
        s.calls += 1
        if s.error:
            raise s.error
        return s.catalog

    fake_client = MagicMock()
    fake_client.get_catalog.side_effect = get_catalog
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value=URL), \
         patch("app.api.routes.laminus.LaminusSidecarClient", return_value=fake_client):
        yield s


# ---------------------------------------------------------------------------
# GET /catalog and get_cached_catalog
# ---------------------------------------------------------------------------

async def test_catalog_is_served_verbatim_from_the_cache_without_contacting_the_sidecar(client, sidecar):
    lmod._catalog_bytes = b'{"cached": "bytes"}'

    resp = await client.get("/api/v1/laminus/catalog")

    assert (resp.status_code, resp.content) == (200, b'{"cached": "bytes"}')
    assert resp.headers["content-type"] == "application/json"
    assert sidecar.calls == 0


async def test_cold_catalog_is_fetched_once_then_cached(client, sidecar):
    first = await client.get("/api/v1/laminus/catalog")
    second = await client.get("/api/v1/laminus/catalog")

    assert first.json() == second.json() == CATALOG
    assert sidecar.calls == 1
    assert lmod._catalog_dict == CATALOG and lmod._catalog_fetched_at is not None


async def test_catalog_503_when_no_sidecar_is_configured_and_502_when_it_is_unreachable(client, sidecar):
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value=None):
        unconfigured = await client.get("/api/v1/laminus/catalog")
    sidecar.error = SidecarError("connection refused")
    unreachable = await client.get("/api/v1/laminus/catalog")

    assert (unconfigured.status_code, unreachable.status_code) == (503, 502)
    assert "not configured" in unconfigured.json()["detail"]
    assert unreachable.json()["detail"] == "Laminus sidecar unreachable: connection refused"
    assert lmod._catalog_bytes is None  # a failed fetch leaves the cache cold


async def test_get_cached_catalog_returns_the_warm_dict_and_fetches_only_when_cold(sidecar):
    assert await lmod.get_cached_catalog() == CATALOG
    sidecar.catalog = {"machine": [], "process": [], "filament": []}  # would differ if re-fetched
    assert await lmod.get_cached_catalog() == CATALOG
    assert sidecar.calls == 1


# ---------------------------------------------------------------------------
# warm_catalog_cache (startup)
# ---------------------------------------------------------------------------

async def test_warm_up_is_a_noop_without_a_sidecar_url():
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value=None), \
         patch("app.api.routes.laminus._fetch_and_cache", new=AsyncMock()) as fetch:
        await lmod.warm_catalog_cache()
    fetch.assert_not_called()


async def test_warm_up_caches_the_catalog_on_the_first_try(sidecar):
    await lmod.warm_catalog_cache()

    assert lmod._catalog_dict == CATALOG and sidecar.calls == 1


@pytest.mark.parametrize("transient", ["503 Service Unavailable", "building_catalog", "502 Bad Gateway"])
async def test_warm_up_waits_and_retries_while_the_sidecar_is_still_building(transient):
    fetch = AsyncMock(side_effect=[RuntimeError(transient), RuntimeError(transient), b"{}"])
    sleeps = AsyncMock()

    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value=URL), \
         patch("app.api.routes.laminus._fetch_and_cache", fetch), \
         patch("app.api.routes.laminus.time.time", advancing_clock(1.0)), \
         patch("app.api.routes.laminus.asyncio.sleep", sleeps):
        await lmod.warm_catalog_cache()

    assert fetch.await_count == 3
    assert [c.args for c in sleeps.await_args_list] == [(5,), (5,)]


async def test_warm_up_gives_up_immediately_on_a_non_transient_error(caplog):
    fetch = AsyncMock(side_effect=RuntimeError("401 unauthorized"))
    sleeps = AsyncMock()

    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value=URL), \
         patch("app.api.routes.laminus._fetch_and_cache", fetch), \
         patch("app.api.routes.laminus.time.time", advancing_clock(10.0)), \
         patch("app.api.routes.laminus.asyncio.sleep", sleeps), caplog.at_level("WARNING", logger="app.laminus"):
        await lmod.warm_catalog_cache()

    assert fetch.await_count == 1
    sleeps.assert_not_called()
    assert "Startup catalog warm-up failed: 401 unauthorized" in caplog.text


async def test_warm_up_stops_after_five_minutes_of_a_sidecar_that_never_finishes_building(caplog):
    fetch = AsyncMock(side_effect=RuntimeError("503"))
    ticks = {"n": 0}

    def clock():  # 1st call sets the 300 s deadline, 2nd (loop check) is inside it, everything later is past it
        ticks["n"] += 1
        return {1: 0.0, 2: 10.0}.get(ticks["n"], 400.0)  # never exhausts: logging also reads time.time()

    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value=URL), \
         patch("app.api.routes.laminus._fetch_and_cache", fetch), \
         patch("app.api.routes.laminus.asyncio.sleep", new=AsyncMock()), \
         patch("app.api.routes.laminus.time.time", clock), caplog.at_level("WARNING", logger="app.laminus"):
        await lmod.warm_catalog_cache()

    assert fetch.await_count == 1
    assert "not ready after 5 minutes" in caplog.text


# ---------------------------------------------------------------------------
# POST /catalog/rescan
# ---------------------------------------------------------------------------

def _health(loaded=True, building=False):
    return MagicMock(json=MagicMock(return_value={"catalog_loaded": loaded, "catalog_building": building}))


@pytest.fixture
def rescan(client, sidecar):
    """`await rescan(trigger=..., health=[...])`: scripts GET /api/profiles?refresh=true then /api/health polls."""
    async def _run(trigger=None, health=()):
        trigger = trigger or MagicMock(status_code=200)
        polls = iter(health)
        seen = []

        def fake_get(url, timeout=None):
            seen.append(url)
            if "/api/profiles" in url:
                if isinstance(trigger, Exception):
                    raise trigger
                return trigger
            # (never let StopIteration escape: raised into an executor future it deadlocks the await)
            nxt = next(polls, _health(loaded=False, building=True))
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        with patch("app.api.routes.laminus.httpx.get", fake_get), \
             patch("app.api.routes.laminus.time.time", advancing_clock(10.0)), \
             patch("app.api.routes.laminus.asyncio.sleep", new=AsyncMock()):
            resp = await client.post("/api/v1/laminus/catalog/rescan")
        return resp, seen
    return _run


async def test_rescan_triggers_a_rebuild_waits_for_it_then_refreshes_the_cache(rescan, sidecar):
    resp, seen = await rescan(trigger=MagicMock(status_code=503),   # 503 = rebuild already running: accepted
                              health=[_health(loaded=False, building=True),
                                      _health(loaded=False, building=False),   # neither building nor loaded: keep waiting
                                      _health(loaded=True, building=False)])

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "bytes": len(json.dumps(CATALOG))}
    assert seen == [f"{URL}/api/profiles?refresh=true"] + [f"{URL}/api/health"] * 3
    assert lmod._catalog_dict == CATALOG


async def test_rescan_503_when_no_sidecar_is_configured(client):
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value=None):
        resp = await client.post("/api/v1/laminus/catalog/rescan")
    assert resp.status_code == 503


async def test_rescan_502_when_the_trigger_is_rejected_or_unreachable(rescan):
    rejected, _ = await rescan(trigger=MagicMock(status_code=500))
    unreachable, _ = await rescan(trigger=httpx.ConnectError("refused"))

    assert (rejected.status_code, rejected.json()["detail"]) == (502, "Laminus rescan trigger returned 500")
    assert unreachable.status_code == 502 and "Could not reach Laminus sidecar" in unreachable.json()["detail"]
    assert lmod._catalog_dict is None


async def test_rescan_504_when_the_rebuild_never_completes(rescan):
    resp, seen = await rescan(health=[])  # the sidecar keeps reporting "still building"

    assert seen.count(f"{URL}/api/health") >= 2  # it did poll before giving up
    assert (resp.status_code, resp.json()["detail"]) == (504, "Laminus catalog rebuild did not complete within 120 s")
    assert lmod._catalog_dict is None


async def test_rescan_keeps_polling_through_health_errors(rescan):
    resp, seen = await rescan(health=[RuntimeError("flaky"), _health(loaded=True, building=False)])

    assert resp.status_code == 200
    assert seen.count(f"{URL}/api/health") == 2


async def test_rescan_reports_pending_remaps_instead_of_swapping_a_catalog_that_drops_used_presets(rescan, sidecar, create_printer):
    lmod._catalog_dict = {"machine": [{"name": "Old Machine", "uuid": "m0"}], "process": [], "filament": []}
    lmod._catalog_bytes = b"old"
    await create_printer(current_orca_printer_profile="Old Machine")
    sidecar.catalog = CATALOG  # the rebuilt catalog no longer has "Old Machine"

    resp, _ = await rescan(health=[_health()])

    body = resp.json()
    assert (resp.status_code, body["status"]) == (200, "pending_remaps") and body["sync_id"]
    assert lmod._catalog_bytes == b"old"  # the old catalog stays active until the operator confirms
    assert lmod._pending_sync["sync_id"] == body["sync_id"] and lmod._pending_sync["catalog"] == CATALOG
