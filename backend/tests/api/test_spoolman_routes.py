"""GET /spoolman/{filaments,spools,sync-status} and the sync-now status bookkeeping."""
import httpx
import pytest

from tests import spoolman_mock

_STATUS_KEYS = {"enabled", "interval_minutes", "last_sync_at", "last_attempt_at", "last_error", "last_error_code"}


async def _configure(client, **fields) -> None:
    body = {"enabled": True, "url": "http://spoolman.test", "api_key": None, **fields}
    assert (await client.put("/api/v1/settings/spoolman", json=body)).status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/spoolman/filaments", "/api/v1/spoolman/spools"])
async def test_reads_are_503_until_spoolman_is_configured_and_enabled(client, spoolman_upstream, path):
    assert (await client.get(path)).status_code == 503  # nothing configured

    await _configure(client, enabled=False)
    resp = await client.get(path)

    assert (resp.status_code, resp.json()["detail"]) == (503, "Spoolman not configured or disabled")
    assert spoolman_upstream.requests == []  # never even contacted


async def test_filaments_and_spools_pass_the_upstream_data_through(client, spoolman_upstream):
    await _configure(client, api_key="s3cret")

    filaments = await client.get("/api/v1/spoolman/filaments")
    spools = await client.get("/api/v1/spoolman/spools")

    assert (filaments.status_code, filaments.json()) == (200, spoolman_mock._FILAMENTS)
    assert (spools.status_code, spools.json()) == (200, spoolman_mock._SPOOLS)
    assert [r.headers["x-api-key"] for r in spoolman_upstream.requests] == ["s3cret", "s3cret"]
    # the fields the frontend reads (src/api/spoolman.ts ApiFilament / ApiSpool)
    assert {"id", "name", "material", "color_hex"} <= set(filaments.json()[0])
    assert {"id", "remaining_weight", "filament"} <= set(spools.json()[0])
    assert spools.json()[0]["filament"]["vendor"]["name"] == "Elegoo"


@pytest.mark.parametrize("path", ["/api/v1/spoolman/filaments", "/api/v1/spoolman/spools"])
async def test_reads_turn_upstream_failures_into_503(client, spoolman_upstream, path):
    await _configure(client)

    spoolman_upstream.handler = lambda request: httpx.Response(500, text="boom")
    http_error = await client.get(path)

    def _refused(request):
        raise httpx.ConnectError("connection refused", request=request)
    spoolman_upstream.handler = _refused
    refused = await client.get(path)

    assert http_error.status_code == 503 and "500" in http_error.json()["detail"]
    assert (refused.status_code, refused.json()["detail"]) == (503, "connection refused")


async def test_sync_status_defaults_when_spoolman_was_never_set_up(client):
    resp = await client.get("/api/v1/spoolman/sync-status")

    assert resp.status_code == 200  # never 503s
    assert resp.json() == {"enabled": False, "interval_minutes": 15, "last_sync_at": None,
                           "last_attempt_at": None, "last_error": None, "last_error_code": None}


async def test_sync_status_reports_disabled_config_without_erroring(client):
    await _configure(client, enabled=False, sync_interval_minutes=30)

    body = (await client.get("/api/v1/spoolman/sync-status")).json()

    assert set(body) == _STATUS_KEYS
    assert (body["enabled"], body["interval_minutes"], body["last_attempt_at"]) == (False, 30, None)


async def test_sync_status_follows_success_then_failure_then_recovery(client, spoolman_upstream):
    await _configure(client, sync_interval_minutes=5)

    ok = await client.post("/api/v1/spoolman/sync-now")
    assert (ok.status_code, ok.json()) == (200, {"filament_count": 2, "spool_count": 2})
    after_ok = (await client.get("/api/v1/spoolman/sync-status")).json()
    assert set(after_ok) == _STATUS_KEYS
    assert after_ok["enabled"] is True and after_ok["interval_minutes"] == 5
    assert after_ok["last_sync_at"] is not None and after_ok["last_sync_at"] == after_ok["last_attempt_at"]
    assert (after_ok["last_error"], after_ok["last_error_code"]) == (None, None)

    spoolman_upstream.handler = lambda request: httpx.Response(500, text="boom")
    failed = await client.post("/api/v1/spoolman/sync-now")
    assert failed.status_code == 503
    after_fail = (await client.get("/api/v1/spoolman/sync-status")).json()
    assert after_fail["last_error_code"] == "500" and "500" in after_fail["last_error"]
    assert after_fail["last_sync_at"] == after_ok["last_sync_at"]  # the last success is remembered
    assert after_fail["last_attempt_at"] > after_ok["last_attempt_at"]

    spoolman_upstream.handler = None  # upstream recovers
    assert (await client.post("/api/v1/spoolman/sync-now")).status_code == 200
    after_recovery = (await client.get("/api/v1/spoolman/sync-status")).json()
    assert (after_recovery["last_error"], after_recovery["last_error_code"]) == (None, None)
    assert after_recovery["last_sync_at"] > after_ok["last_sync_at"]
