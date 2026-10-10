"""`/api/v1/webhooks` destinations (BIZ-172) and the legacy `/settings/webhook` view of the `default` one."""
import pytest

from app.services import webhook_service
from tests.webhook_helpers import install_wire, verify_signature

URL = "https://hooks.example.test/a"


@pytest.fixture
def wire(monkeypatch):
    return install_wire(monkeypatch)


async def test_create_list_get_patch_delete_a_destination_and_the_secret_never_comes_back(client):
    body = {"name": "shop", "url": URL, "secret": "whsec", "events": ["project.created", "job.complete"], "enabled": True}
    created = await client.post("/api/v1/webhooks", json=body)
    assert created.status_code == 201
    d = created.json()
    assert (d["name"], d["url"], d["has_secret"], d["events"], d["enabled"]) == ("shop", URL, True, ["project.created", "job.complete"], True)
    assert "whsec" not in created.text and "secret" not in d

    assert [x["id"] for x in (await client.get("/api/v1/webhooks")).json()] == [d["id"]]
    assert (await client.get(f"/api/v1/webhooks/{d['id']}")).json()["name"] == "shop"

    patched = (await client.patch(f"/api/v1/webhooks/{d['id']}", json={"enabled": False, "events": []})).json()
    assert (patched["enabled"], patched["events"], patched["has_secret"], patched["url"]) == (False, [], True, URL)   # secret kept
    cleared = (await client.patch(f"/api/v1/webhooks/{d['id']}", json={"secret": ""})).json()
    assert cleared["has_secret"] is False

    assert (await client.delete(f"/api/v1/webhooks/{d['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/webhooks/{d['id']}")).status_code == 404
    assert (await client.delete(f"/api/v1/webhooks/{d['id']}")).status_code == 404


@pytest.mark.parametrize("bad,why", [
    ({"name": "x", "url": "ftp://nope"}, "url"), ({"name": "x", "url": "not a url"}, "url"),
    ({"name": "x", "events": ["Not An Event"]}, "events"), ({"name": ""}, "name"),
])
async def test_invalid_destinations_are_rejected(client, bad, why):
    resp = await client.post("/api/v1/webhooks", json=bad)
    assert resp.status_code == 422 and why in resp.text


async def test_destination_names_are_unique(client):
    assert (await client.post("/api/v1/webhooks", json={"name": "a", "url": URL})).status_code == 201
    assert (await client.post("/api/v1/webhooks", json={"name": "a", "url": URL})).status_code == 409
    other = (await client.post("/api/v1/webhooks", json={"name": "b", "url": URL})).json()
    assert (await client.patch(f"/api/v1/webhooks/{other['id']}", json={"name": "a"})).status_code == 409


async def test_test_endpoint_sends_one_signed_event_even_to_a_disabled_destination(client, wire):
    d = (await client.post("/api/v1/webhooks", json={"name": "t", "url": URL, "secret": "whsec", "enabled": False,
                                                       "events": ["job.failed"]})).json()

    resp = await client.post(f"/api/v1/webhooks/{d['id']}/test")

    assert resp.json() == {"ok": True, "status": 200, "error": None}
    (request,) = wire.requests
    assert verify_signature("whsec", request) and wire.payloads()[0]["event"] == "webhook.test"
    assert (await client.get(f"/api/v1/webhooks/{d['id']}")).json()["last_status"] == 200


async def test_test_endpoint_reports_a_failing_receiver_without_retrying(client, wire):
    d = (await client.post("/api/v1/webhooks", json={"name": "t", "url": URL})).json()
    wire.default = 500

    resp = await client.post(f"/api/v1/webhooks/{d['id']}/test")

    assert resp.json() == {"ok": False, "status": 500, "error": "HTTP 500"} and len(wire.requests) == 1
    no_url = (await client.post("/api/v1/webhooks", json={"name": "n"})).json()
    assert (await client.post(f"/api/v1/webhooks/{no_url['id']}/test")).status_code == 422
    assert (await client.post("/api/v1/webhooks/999/test")).status_code == 404


async def test_the_legacy_settings_webhook_is_the_default_destination(client, wire):
    assert (await client.get("/api/v1/settings/webhook")).json() == {"url": None, "has_secret": False, "events": []}
    put = await client.put("/api/v1/settings/webhook", json={"url": URL, "secret": "s", "events": ["job.complete"]})
    assert put.json() == {"url": URL, "has_secret": True, "events": ["job.complete"]}

    (default,) = [d for d in (await client.get("/api/v1/webhooks")).json() if d["name"] == "default"]
    assert (default["url"], default["has_secret"], default["events"], default["enabled"]) == (URL, True, ["job.complete"], True)
    await client.post("/api/v1/webhooks", json={"name": "extra", "url": "https://other.test/h"})      # a second one is independent
    assert (await client.get("/api/v1/settings/webhook")).json()["url"] == URL


async def test_scopes_a_key_with_only_read_cannot_write(client, session_factory):
    from app.models import ApiKey
    from app.services.api_key_service import generate_key, hash_key
    raw, prefix = generate_key()
    async with session_factory() as s:
        s.add(ApiKey(name="ro", key_prefix=prefix, key_hash=hash_key(raw), scopes=["settings:read"], enabled=True,
                     created_at="2026-01-01T00:00:00"))
        await s.commit()
    headers = {"X-Api-Key": raw}
    assert (await client.get("/api/v1/webhooks", headers=headers)).status_code == 200
    assert (await client.post("/api/v1/webhooks", json={"name": "x"}, headers=headers)).status_code == 403
