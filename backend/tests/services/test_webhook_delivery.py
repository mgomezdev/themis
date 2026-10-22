"""Webhook delivery contract (BIZ-172): signed, bounded retries with a stable event id, independent destinations."""
import httpx
import pytest

from app.models import WebhookDestination
from app.services import webhook_service
from tests.webhook_helpers import destination, install_wire, verify_signature


@pytest.fixture
def wire(monkeypatch):
    return install_wire(monkeypatch)


async def _dispatch(session_factory, event="job.complete", **kw):
    async with session_factory() as s:
        n = await webhook_service.dispatch(s, event, kw.pop("job_id", 5), kw.pop("extra", None), **kw)
    await webhook_service.drain()
    return n


async def _row(session_factory, name="default") -> WebhookDestination:
    from sqlalchemy import select
    async with session_factory() as s:
        return (await s.execute(select(WebhookDestination).where(WebhookDestination.name == name))).scalar_one()


async def test_a_delivery_is_signed_and_carries_a_stable_event_id_and_schema_version(wire, session_factory):
    async with session_factory() as s:
        s.add(destination(url="https://a.test/hook", secret="whsec"))
        await s.commit()

    assert await _dispatch(session_factory, extra={"project_id": 9}) == 1

    (request,) = wire.requests
    (payload,) = wire.payloads()
    assert verify_signature("whsec", request)
    assert not verify_signature("wrong-secret", request)                       # an invalid signature does not verify
    assert payload["schema_version"] == 1 and payload["event"] == "job.complete" and payload["project_id"] == 9
    assert request.headers["x-webhook-id"] == payload["event_id"] and request.headers["x-webhook-event"] == "job.complete"


async def test_a_network_timeout_is_retried_with_the_same_body_and_event_id_then_succeeds(wire, session_factory):
    async with session_factory() as s:
        s.add(destination(url="https://a.test/hook", secret="whsec"))
        await s.commit()
    wire.responses = [httpx.ReadTimeout("timed out"), 503]

    await _dispatch(session_factory)

    assert len(wire.requests) == 3                                              # timeout, 503, then the default 200
    assert len({r.content for r in wire.requests}) == 1                         # byte-identical body (so the signature is too)
    assert len({r.headers["x-webhook-id"] for r in wire.requests}) == 1         # receivers deduplicate on this
    row = await _row(session_factory)
    assert (row.last_status, row.last_error) == (200, None) and row.last_success_at is not None


async def test_retries_are_bounded_and_the_final_failure_is_recorded_on_the_destination(wire, session_factory):
    async with session_factory() as s:
        s.add(destination(url="https://a.test/hook"))
        await s.commit()
    wire.default = 500

    await _dispatch(session_factory)

    assert len(wire.requests) == webhook_service.MAX_ATTEMPTS == 3
    row = await _row(session_factory)
    assert (row.last_status, row.last_error, row.last_success_at) == (500, "HTTP 500", None)


async def test_a_client_error_is_final_and_a_429_is_retried(wire, session_factory):
    async with session_factory() as s:
        s.add(destination(url="https://a.test/hook"))
        await s.commit()
    wire.default = 400
    await _dispatch(session_factory)
    assert len(wire.requests) == 1

    wire.requests.clear()
    wire.responses = [429]
    await _dispatch(session_factory)
    assert len(wire.requests) == 2


async def test_transport_error_text_never_leaks_the_secret(wire, session_factory):
    async with session_factory() as s:
        s.add(destination(url="https://a.test/hook", secret="top-secret-value"))
        await s.commit()
    wire.default = httpx.ConnectError("refused while signing with top-secret-value")

    await _dispatch(session_factory)

    assert "top-secret-value" not in (await _row(session_factory)).last_error


async def test_destinations_are_independent_secret_filter_enabled_and_failure(wire, session_factory):
    async with session_factory() as s:
        s.add(destination(name="alpha", url="https://alpha.test/h", secret="alpha-secret"))
        s.add(destination(name="beta", url="https://beta.test/h", secret="beta-secret", events=["job.failed"]))
        s.add(destination(name="gamma", url="https://gamma.test/h", enabled=False))
        s.add(destination(name="delta", url="https://delta.test/h", secret=None, events=["job.complete"]))
        await s.commit()
    wire.by_host = {"delta.test": 500}                                          # one endpoint is down

    assert await _dispatch(session_factory, "job.complete") == 2                # alpha + delta; beta filtered, gamma disabled

    hosts = [r.url.host for r in wire.requests]
    assert sorted(set(hosts)) == ["alpha.test", "delta.test"] and hosts.count("alpha.test") == 1 and hosts.count("delta.test") == 3
    (alpha,) = [r for r in wire.requests if r.url.host == "alpha.test"]
    assert verify_signature("alpha-secret", alpha) and not verify_signature("beta-secret", alpha)
    delta = [r for r in wire.requests if r.url.host == "delta.test"][0]
    assert "x-webhook-signature" not in delta.headers
    assert (await _row(session_factory, "alpha")).last_status == 200 and (await _row(session_factory, "delta")).last_status == 500
    assert (await _row(session_factory, "beta")).last_attempt_at is None and (await _row(session_factory, "gamma")).last_attempt_at is None


async def test_every_destination_gets_the_same_event_id_for_one_event(wire, session_factory):
    async with session_factory() as s:
        s.add(destination(name="one", url="https://one.test/h"))
        s.add(destination(name="two", url="https://two.test/h"))
        await s.commit()

    await _dispatch(session_factory)

    assert len({p["event_id"] for p in wire.payloads()}) == 1 and len(wire.requests) == 2


async def test_a_destination_without_a_url_receives_nothing(wire, session_factory):
    async with session_factory() as s:
        s.add(destination(url=None))
        await s.commit()
    assert await _dispatch(session_factory) == 0 and wire.requests == []
