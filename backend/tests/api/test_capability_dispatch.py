"""`/api/v1/capabilities/{cap}/...` dispatches to the active provider's exposed routes (same handlers, same dependencies,
same scopes as `/api/v1/plugins/{id}/...`)."""
import asyncio

import pytest
from fastapi import APIRouter, Depends
from fastapi.responses import Response, StreamingResponse
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app import plugins
from app.api.routes import capabilities as capabilities_routes
from app.auth import require_scope
from app.main import app
from app.models import ApiKey
from app.plugins.host import plugin_host
from app.plugins.manifest import Provide
from app.services.api_key_service import generate_key, hash_key
from tests.plugins.dummy_plugin import make_manifest

CAP = "dummy_one.ping"
STREAM_GATE: asyncio.Event | None = None      # the /stream route holds its second chunk until the test opens this


class Item(BaseModel):
    name: str


def _router(answer: str) -> APIRouter:
    r = APIRouter()

    @r.get("/echo", dependencies=[Depends(require_scope("inventory:read"))])
    async def echo():
        return {"echo": answer}

    @r.post("/items", status_code=201, dependencies=[Depends(require_scope("inventory:write"))])
    async def add(body: Item):
        return {"name": body.name}

    @r.get("/cookies")
    async def cookies():
        resp = Response(content=b"{}", media_type="application/json")
        resp.headers.append("set-cookie", "a=1")
        resp.headers.append("set-cookie", "b=2")
        return resp

    @r.get("/stream")
    async def stream():
        async def chunks():
            yield b"one"
            await STREAM_GATE.wait()
            yield b"two"
        return StreamingResponse(chunks())

    @r.get("/boom")
    async def boom():
        raise RuntimeError("route failed before responding")

    @r.put("/provider")
    async def shadowed():
        return {"reached": "plugin"}

    return r


def _private_router() -> APIRouter:
    r = APIRouter()

    @r.get("/private", dependencies=[Depends(require_scope("inventory:read"))])
    async def private():
        return {"private": True}

    return r


@pytest.fixture
def dispatch_plugins(client):
    """dummy_one (defines + provides CAP, exposes /echo and /items, has a plain /private) and dummy_two (also provides CAP)."""
    routes_before = list(app.router.routes)
    one = make_manifest("dummy_one", provides={CAP: Provide(routers=(_router("ok"),))}, routers=(_private_router(),))
    two = make_manifest("dummy_two", provides={CAP: Provide(routers=(_router("two"),))}, defines=())
    for m in (one, two):
        plugins.register_plugin(m)
        capabilities_routes.mount_plugin(app, m)
    added = app.router.routes[len(routes_before):]
    app.router.routes[:] = [*added, *routes_before]           # ahead of the SPA catch-all, as at import time in production
    yield
    app.router.routes[:] = routes_before
    app.openapi_schema = None                                  # the schema may have been cached with the test routes in it
    for key in [(m.id, CAP) for m in (one, two)]:
        capabilities_routes._EXPOSED.pop(key, None)


async def test_dispatch_reaches_the_active_plugins_route_with_overrides(client, dispatch_plugins):
    await plugin_host.set_provider(CAP, "dummy_one")
    r = await client.get(f"/api/v1/capabilities/{CAP}/echo")
    assert r.status_code == 200 and r.json() == {"echo": "ok"}
    assert (await client.get("/api/v1/plugins/dummy_one/echo")).json() == r.json()            # identical to the plugin-id route


async def test_409_capability_unavailable_when_nothing_is_selected(client, dispatch_plugins):
    r = await client.get(f"/api/v1/capabilities/{CAP}/echo")
    assert r.status_code == 409 and r.json()["error"] == "capability_unavailable" and r.json()["capability"] == CAP


async def test_404_when_the_active_provider_does_not_expose_the_path(client, dispatch_plugins):
    await plugin_host.set_provider(CAP, "dummy_one")
    assert (await client.get(f"/api/v1/capabilities/{CAP}/nothing-here")).status_code == 404
    # `/private` is in the manifest's plain `routers` (not in Provide.routers): reachable by plugin id, never through the capability
    assert (await client.get("/api/v1/plugins/dummy_one/private")).status_code == 200
    assert (await client.get(f"/api/v1/capabilities/{CAP}/private")).status_code == 404


async def test_path_traversal_cannot_reach_another_plugins_routes(client, dispatch_plugins):
    await plugin_host.set_provider(CAP, "dummy_one")
    # The client resolves a literal `../` itself, so that request lands on the (inactive, unknown) capability "dummy_two": 409.
    # The encoded forms reach the dispatcher with `..` segments, which it refuses outright: 404.
    expected = {"../dummy_two/echo": 409, "%2e%2e/dummy_two/echo": 404, "x/%2e%2e/%2e%2e/dummy_two/echo": 404}
    for rest, status in expected.items():
        r = await client.get(f"/api/v1/capabilities/{CAP}/{rest}")
        assert r.status_code == status, (rest, r.status_code, r.text)
        assert "echo" not in r.json() and r.json().get("error") in (None, "capability_unavailable")     # never a route's own answer


async def test_scope_is_enforced_through_the_dispatcher(client, dispatch_plugins, session_factory):
    await plugin_host.set_provider(CAP, "dummy_one")
    raw, prefix = generate_key()
    async with session_factory() as s:                                  # a key WITHOUT inventory:read
        s.add(ApiKey(name="noscope", key_prefix=prefix, key_hash=hash_key(raw), scopes=["settings:read"], enabled=True,
                     created_at="2026-01-01T00:00:00"))
        await s.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-Api-Key": raw}) as c:
        assert (await c.get(f"/api/v1/capabilities/{CAP}/echo")).status_code == 403
        assert (await c.get("/api/v1/plugins/dummy_one/echo")).status_code == 403              # same answer as the plugin-id route


async def test_dispatch_follows_the_selection_switch_without_a_restart(client, dispatch_plugins):
    await plugin_host.set_provider(CAP, "dummy_one")
    assert (await client.get(f"/api/v1/capabilities/{CAP}/echo")).json() == {"echo": "ok"}
    await plugin_host.set_provider(CAP, "dummy_two")
    assert (await client.get(f"/api/v1/capabilities/{CAP}/echo")).json() == {"echo": "two"}


async def test_methods_and_bodies_pass_through(client, dispatch_plugins):
    await plugin_host.set_provider(CAP, "dummy_one")
    ok = await client.post(f"/api/v1/capabilities/{CAP}/items", json={"name": "a"})
    assert ok.status_code == 201 and ok.json() == {"name": "a"}
    bad = await client.post(f"/api/v1/capabilities/{CAP}/items", json={"nope": 1})
    assert bad.status_code == 422 and bad.json()["detail"][0]["loc"][-1] == "name"                # the plugin route's own validation error


async def test_a_wrong_method_is_405_not_a_crash(client, dispatch_plugins):
    await plugin_host.set_provider(CAP, "dummy_one")
    assert (await client.delete(f"/api/v1/capabilities/{CAP}/echo")).status_code == 405


async def test_the_dispatcher_is_not_in_the_openapi_document(client, dispatch_plugins):
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert not any("{rest}" in p for p in paths)
    assert "/api/v1/capabilities/{cap}/provider" in paths                                          # the explicit routes still are


async def test_a_bundled_provider_is_reachable_through_its_capability(client):
    await plugin_host.set_provider("inventory.filament", "local_inventory")
    via_capability = await client.get("/api/v1/capabilities/inventory.filament/weight-log")
    assert via_capability.status_code == 200 and via_capability.json() == []
    assert (await client.get("/api/v1/plugins/local_inventory/weight-log")).json() == via_capability.json()
    await plugin_host.set_provider("inventory.filament", None)
    assert (await client.get("/api/v1/capabilities/inventory.filament/weight-log")).status_code == 409


async def test_the_dispatcher_needs_a_key_whether_or_not_a_provider_is_active(client, dispatch_plugins):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get(f"/api/v1/capabilities/{CAP}/echo")).status_code == 401          # nothing selected: no 409 for strangers
        await plugin_host.set_provider(CAP, "dummy_one")
        assert (await anon.get(f"/api/v1/capabilities/{CAP}/echo")).status_code == 401          # selected: still 401 up front
        assert (await anon.get("/api/v1/capabilities/nope.nothing/x")).status_code == 401


async def test_repeated_response_headers_survive_the_dispatcher(client, dispatch_plugins):
    await plugin_host.set_provider(CAP, "dummy_one")
    r = await client.get(f"/api/v1/capabilities/{CAP}/cookies")
    assert r.status_code == 200 and r.headers.get_list("set-cookie") == ["a=1", "b=2"]


async def test_a_streaming_route_is_streamed_not_buffered(client, dispatch_plugins, session_factory):
    global STREAM_GATE
    STREAM_GATE = asyncio.Event()
    await plugin_host.set_provider(CAP, "dummy_one")
    raw, prefix = generate_key()
    async with session_factory() as s:
        s.add(ApiKey(name="stream", key_prefix=prefix, key_hash=hash_key(raw), scopes=["settings:read"], enabled=True,
                     created_at="2026-01-01T00:00:00"))
        await s.commit()
    path = f"/api/v1/capabilities/{CAP}/stream"
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
             "raw_path": path.encode(), "root_path": "", "query_string": b"", "server": ("test", 80), "client": ("t", 1),
             "headers": [(b"x-api-key", raw.encode()), (b"host", b"test")]}
    sent: asyncio.Queue = asyncio.Queue()

    async def receive():
        await asyncio.sleep(3600)

    async def send(message):
        await sent.put(message)

    task = asyncio.create_task(app(scope, receive, send))
    try:
        assert (await asyncio.wait_for(sent.get(), 5))["status"] == 200
        assert (await asyncio.wait_for(sent.get(), 5))["body"] == b"one"      # arrives while the route is still blocked
        assert not task.done()
        STREAM_GATE.set()
        assert (await asyncio.wait_for(sent.get(), 5))["body"] == b"two"
    finally:
        STREAM_GATE.set()
        task.cancel()


async def test_a_plugin_put_provider_route_is_not_exposed_because_it_would_be_shadowed(client, dispatch_plugins):
    assert capabilities_routes._EXPOSED[("dummy_one", CAP)] and all(
        "PUT" not in methods or not regex.match("/api/v1/plugins/dummy_one/provider")
        for regex, methods in capabilities_routes._EXPOSED[("dummy_one", CAP)])
    r = await client.put(f"/api/v1/capabilities/{CAP}/provider", json={"plugin_id": "dummy_one"})
    assert r.json().get("reached") is None and r.json()["plugin_id"] == "dummy_one"          # the selection route answered
    assert (await client.put("/api/v1/plugins/dummy_one/provider")).json() == {"reached": "plugin"}   # still mounted by id


async def test_a_route_that_raises_before_responding_does_not_hang_the_dispatcher(client, dispatch_plugins, session_factory):
    await plugin_host.set_provider(CAP, "dummy_one")
    raw, prefix = generate_key()
    async with session_factory() as s:
        s.add(ApiKey(name="boom", key_prefix=prefix, key_hash=hash_key(raw), scopes=["settings:read"], enabled=True,
                     created_at="2026-01-01T00:00:00"))
        await s.commit()
    path = f"/api/v1/capabilities/{CAP}/boom"
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
             "raw_path": path.encode(), "root_path": "", "query_string": b"", "server": ("test", 80), "client": ("t", 1),
             "headers": [(b"x-api-key", raw.encode()), (b"host", b"test")]}
    sent = []

    async def receive():
        await asyncio.sleep(3600)

    async def send(message):
        sent.append(message)

    try:
        await asyncio.wait_for(app(scope, receive, send), 5)         # a hang here is the failure
    except RuntimeError:
        pass                                                         # the server-error middleware re-raises after answering
    assert sent and sent[0]["status"] == 500
