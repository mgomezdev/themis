"""Golden responses for everything the Spoolman-to-plugin extraction (BIZ-202) must keep byte-identical (BIZ-204).

Each test drives today's code against the mock Spoolman and pins `{status, body}` (or the outgoing payload) in
`tests/golden/spoolman/`. Phase 1c+ replays the same calls through the plugin's alias routes and compares against
these files. Regenerate deliberately with `UPDATE_GOLDEN=1`. Timestamps are masked; everything else is exact."""
import copy
from unittest.mock import AsyncMock, patch

import pytest

from app.models import InventoryConfig
from app.services import notification_service, webhook_service
from app.services.inventory.sync import record_sync
from tests import spoolman_mock
from tests.api.test_jobs_api import _seed_spool_warning_fixture
from tests.api.test_queue_api import _seed_queue_spool_warning_fixture
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import enable_spoolman, spool as make_spool, use_provider
from tests.golden import assert_golden, mask
from tests.waiting import wait_until

_TIMESTAMPS = {"last_sync_at", "last_attempt_at", "timestamp"}


@pytest.fixture(autouse=True)
def _restore_spoolman_mock():
    saved = (copy.deepcopy(spoolman_mock._FILAMENTS), copy.deepcopy(spoolman_mock._SPOOLS))
    yield
    spoolman_mock._FILAMENTS[:] = saved[0]
    spoolman_mock._SPOOLS[:] = saved[1]


def _rec(resp) -> dict:
    return {"status": resp.status_code, "body": mask(resp.json(), _TIMESTAMPS)}


async def _configure(client, **fields):
    body = {"enabled": True, "url": "http://spoolman.test", "api_key": "s3cret", **fields}
    return await client.put("/api/v1/settings/spoolman", json=body)


async def test_golden_filaments_and_spools(client, spoolman_upstream):
    unconfigured = await client.get("/api/v1/spoolman/filaments")
    await _configure(client)
    assert_golden("spoolman/filaments", {
        "unconfigured": _rec(unconfigured),
        "ok": _rec(await client.get("/api/v1/spoolman/filaments"))})
    assert_golden("spoolman/spools", {"ok": _rec(await client.get("/api/v1/spoolman/spools"))})


async def test_golden_sync_now_and_sync_status(client, spoolman_upstream):
    never = _rec(await client.get("/api/v1/spoolman/sync-status"))
    await _configure(client, sync_interval_minutes=5)
    synced = _rec(await client.post("/api/v1/spoolman/sync-now"))
    after = _rec(await client.get("/api/v1/spoolman/sync-status"))
    assert_golden("spoolman/sync-now", {"ok": synced})
    assert_golden("spoolman/sync-status", {"never_configured": never, "after_sync": after})


async def test_golden_patch_filament(client, spoolman_upstream):
    await _configure(client)
    resp = await client.patch("/api/v1/spoolman/filaments/2", json={"orca_profiles": {"P1S": ["Bambu PLA @P1S"]}})
    assert_golden("spoolman/filaments-id", {"ok": _rec(resp)})


async def test_golden_low_stock_config(client):
    empty = _rec(await client.get("/api/v1/spoolman/low-stock"))
    put = _rec(await client.put("/api/v1/spoolman/low-stock", json={"default_g": 150, "overrides": {"3": 40.5, "12": 0}}))
    invalid = _rec(await client.put("/api/v1/spoolman/low-stock", json={"default_g": -1}))
    assert_golden("spoolman/low-stock", {"empty": empty, "put": put, "invalid": invalid,
                                         "after": _rec(await client.get("/api/v1/spoolman/low-stock"))})


async def test_golden_settings_spoolman_and_test(client, spoolman_upstream):
    initial = _rec(await client.get("/api/v1/settings/spoolman"))
    nothing_saved = _rec(await client.post("/api/v1/settings/spoolman/test", json={}))
    saved = _rec(await _configure(client))
    tested = _rec(await client.post("/api/v1/settings/spoolman/test", json={}))
    no_url = _rec(await client.post("/api/v1/settings/spoolman/test", json={"url": ""}))
    assert_golden("settings/spoolman", {"initial": initial, "saved": saved})
    assert_golden("settings/spoolman-test", {"saved_config": tested, "blank_url_falls_back_to_saved": no_url,
                                                 "nothing_saved": nothing_saved})


async def test_golden_spool_low_webhook_and_notification(session_factory, spoolman_upstream):
    await enable_spoolman()
    sent, notified = [], []

    async def fire(url, secret, payload):
        sent.append({"url": url, "secret": secret, "payload": mask(payload, _TIMESTAMPS)})

    async def dispatch(notif, event, job_id, title, message):
        notified.append({"event": event, "job_id": job_id, "title": title, "message": message})

    from app.models import NotificationConfig, WebhookConfig
    async with session_factory() as s:
        s.add(InventoryConfig(id=1, deduct_on_complete=True, low_stock_default_g=600.0, low_stock_overrides={},
                              low_stock_alerted=[]))
        s.add(WebhookConfig(id=1, url="http://hook.test/x", secret="whsec"))
        s.add(NotificationConfig(id=1, ntfy_enabled=True))
        await s.commit()
        with patch.object(webhook_service, "fire", fire), patch.object(notification_service, "dispatch", dispatch):
            await record_sync(s)
            await wait_until(lambda: sent and notified)
        assert (await s.get(InventoryConfig, 1)).low_stock_alerted == ["spoolman:2"]

    assert_golden("spoolman/spool-low", {"webhook": sent, "notification": notified})


@pytest.mark.parametrize("grams,remaining,name", [(200.0, 900.0, "sufficient"), (340.0, 220.0, "insufficient")])
async def test_golden_low_stock_warning_on_queue_and_job_details(client, session_factory, grams, remaining, name):
    fake = FakeInventoryProvider(spools=[make_spool("99", remaining, name="Bambu PLA Basic Black", material="PLA")])
    qjob, _ = await _seed_queue_spool_warning_fixture(session_factory, estimate_grams=grams)
    await use_provider(fake)
    queue = (await client.get("/api/v1/queue")).json()
    queue_warning = next(j for j in queue if j["id"] == qjob)["low_stock_warning"]

    jjob, jprinter = await _seed_spool_warning_fixture(session_factory, estimate_grams=grams)
    await use_provider(fake)
    details = (await client.get(f"/api/v1/jobs/{jjob}/details")).json()
    detail_warning = next(c for c in details["printer_configs"] if c["printer_id"] == jprinter)["low_stock_warning"]

    assert_golden(f"spoolman/low-stock-warning-{name}", {"queue": queue_warning, "job_details": detail_warning})
