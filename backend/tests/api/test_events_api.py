"""`/api/v1/events` (BIZ-249): the catalog, per-subscriber counters and the durable deliveries an operator may retry."""
import pytest

from app.api.routes import events as events_routes
from app.eventing import EventEnvelope, hub as hubmod
from app.eventing.hub import EventHub
from tests.eventing.fakes import definer_manifest, register
from tests.waiting import wait_until


@pytest.fixture
async def hub(session_factory, monkeypatch):
    from app.plugins.host import plugin_host
    await plugin_host.start()
    h = EventHub()
    monkeypatch.setattr(events_routes, "hub", h)
    monkeypatch.setattr(hubmod, "BACKOFF_BASE_S", 0.02)
    monkeypatch.setattr(hubmod, "MAX_ATTEMPTS", 2)
    await h.start(session_factory)
    yield h
    await h.stop()


async def test_catalog_lists_core_and_plugin_defined_events_with_their_durability(client):
    register(definer_manifest("pub_cat"))
    rows = {r["name"]: r for r in (await client.get("/api/v1/events/catalog")).json()}

    assert (rows["job.complete"]["durability"], rows["job.complete"]["source"], rows["job.complete"]["version"]) == ("durable", "core", 1)
    assert rows["job.blocked"]["durability"] == "best_effort"
    assert (rows["pub_cat.ready"]["source"], rows["pub_cat.ready"]["durability"]) == ("pub_cat", "best_effort")


async def test_subscribers_report_counters_and_the_redacted_last_error(client, hub):
    async def boom(e):
        raise RuntimeError("login failed password=hunter2")

    hub.subscribe("job.blocked", boom, name="api.boom")
    await hub.publish(EventEnvelope(name="job.blocked", dedup_key="b:1"))
    await hub.drain()

    (row,) = [r for r in (await client.get("/api/v1/events/subscribers")).json() if r["subscriber"] == "core:api.boom"]
    assert (row["event"], row["kind"], row["failed"], row["delivered"], row["durable_pending"]) == ("job.blocked", "core", 1, 0, 0)
    assert "hunter2" not in row["last_error"] and row["last_error"].startswith("RuntimeError")


async def test_dead_deliveries_are_listed_and_can_be_retried(client, hub, session_factory):
    ok = {"v": False}

    async def handler(e):
        if not ok["v"]:
            raise RuntimeError("down")

    hub.subscribe("job.complete", handler, name="api.dur")
    async with session_factory() as s:
        await hub.enqueue_durable(s, EventEnvelope(name="job.complete", entities={"job_id": 5}, dedup_key="job.complete:5"))
        await s.commit()
    hub.wake()

    async def dead():
        return (await client.get("/api/v1/events/deliveries")).json() or None

    (d,) = await wait_until(dead, what="a dead delivery")
    assert (d["name"], d["subscriber"], d["status"], d["attempts"], d["dedup_key"]) == ("job.complete", "core:api.dur", "dead", 2, "job.complete:5")
    sub = next(r for r in (await client.get("/api/v1/events/subscribers")).json() if r["subscriber"] == "core:api.dur")
    assert sub["durable_dead"] == 1

    ok["v"] = True
    assert (await client.post(f"/api/v1/events/deliveries/{d['id']}/retry")).json() == {"id": d["id"], "status": "pending"}
    async def delivered():
        return (await client.get("/api/v1/events/deliveries", params={"status": "delivered"})).json() or None

    (done,) = await wait_until(delivered, what="delivered row")
    assert done["id"] == d["id"] and done["last_error"] is None
    assert (await client.get("/api/v1/events/deliveries")).json() == []


async def test_deliveries_validate_status_and_retry_404s_for_unknown(client, hub):
    assert (await client.get("/api/v1/events/deliveries", params={"status": "weird"})).status_code == 422
    assert (await client.post("/api/v1/events/deliveries/9999/retry")).status_code == 404


async def test_durable_rows_of_an_unregistered_subscriber_are_still_reported(client, hub, session_factory):
    hub.subscribe("job.complete", _noop, name="api.gone")
    async with session_factory() as s:
        await hub.enqueue_durable(s, EventEnvelope(name="job.complete", dedup_key="job.complete:1"))
        await s.commit()
    hub.unsubscribe("api.gone")                                  # e.g. a plugin uninstalled with work still queued
    hub._stats.pop("core:api.gone")

    rows = {r["subscriber"]: r for r in (await client.get("/api/v1/events/subscribers")).json()}
    assert (rows["core:api.gone"]["event"], rows["core:api.gone"]["durable_pending"]) == (None, 1)


async def _noop(e):
    return None
