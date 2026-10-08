"""Capability management API: every known capability (core + plugin-defined), its providers and the selected one."""
from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.routing import compile_path

from ...auth import require_any_key, require_scope
from ...plugins import PluginError, capability_catalog, definer_of, providers_of, registered_plugins
from ...plugins.capabilities.definition import CapabilityDef
from ...plugins.host import plugin_host
from ...plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["capabilities"])


class ProviderBody(BaseModel):
    plugin_id: str | None = None


def _view(cap: CapabilityDef) -> dict:
    st = plugin_host.status(cap.id)
    selected = plugin_host.selected(cap.id)
    providers = [{"plugin_id": m.id, "name": m.name, "version": m.provides[cap.id].version, "enabled": plugin_host.is_enabled(m.id),
                  "status": st.state if selected == m.id else "not_selected", "waiting_on": list(plugin_host.unmet(m.id))}
                 for m in providers_of(cap.id)]
    requires_by = [{"plugin_id": m.id, "min_version": r.min_version}
                   for m in registered_plugins() for r in m.requires if r.capability == cap.id]
    return {"id": cap.id, "version": cap.version, "label": cap.label, "description": cap.description, "definer": definer_of(cap.id),
            "features": sorted(cap.features), "required_methods": list(cap.required_methods), "selected": selected,
            "explicit": plugin_host.is_explicit(cap.id), "status": st.state, "waiting_on": list(st.waiting_on), "error": st.error,
            "providers": providers, "requires_by": requires_by}


@router.get("/capabilities", summary="Every known capability, its providers and the selected one",
            dependencies=[Depends(require_scope("settings:read"))])
async def list_capabilities():
    catalog = capability_catalog()
    items = [_view(d) for d in sorted(catalog.values(), key=lambda d: (definer_of(d.id) is not None, d.id))]
    for cap, pid in sorted(plugin_host.selections().items()):         # dormant: a stored choice whose definer is gone
        if cap not in catalog:
            items.append({"id": cap, "version": 0, "label": cap, "description": "", "definer": None, "features": [],
                          "required_methods": [], "selected": pid, "explicit": plugin_host.is_explicit(cap), "status": "dormant",
                          "waiting_on": [], "error": None, "providers": [], "requires_by": []})
    return {"capabilities": items}


@router.put("/capabilities/{cap}/provider", summary="Choose the provider of a capability (null = none)",
            dependencies=[Depends(require_scope("settings:write"))])
async def set_provider(cap: str, body: ProviderBody):
    try:
        await plugin_host.set_provider(cap, body.plugin_id)
    except PluginError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"capability": cap, "plugin_id": plugin_host.selected(cap), "explicit": True}


# --- mounting + the per-capability REST dispatcher -----------------------------------------------------------------------

# (plugin id, capability) -> (compiled path regex, methods) for every route its `Provide.routers` expose. The dispatcher forwards
# ONLY paths matching one of these. (Matching our own compiled templates, not app.router.routes: newer FastAPI keeps included
# routers as opaque nested entries there.)
_EXPOSED: dict[tuple[str, str], tuple[tuple[re.Pattern[str], frozenset[str]], ...]] = {}


def mount_plugin(app: FastAPI, manifest: PluginManifest) -> None:
    """Mount a plugin's routers under /api/v1/plugins/{id} (each router object once) and remember which paths each of its
    capabilities exposes through /api/v1/capabilities/{cap}/..."""
    prefix, seen = f"/api/v1/plugins/{manifest.id}", set()

    def mount(r) -> None:
        if id(r) not in seen:
            seen.add(id(r))
            app.include_router(r, prefix=prefix)

    for r in manifest.routers:
        mount(r)
    for cap, provide in manifest.provides.items():
        for r in provide.routers:
            mount(r)
        exposed = []
        for r in provide.routers:
            for route in r.routes:
                methods = frozenset(route.methods or ())
                if route.path == "/provider" and "PUT" in methods:   # PUT /capabilities/{cap}/provider is the selection route
                    logger.warning("Plugin %s route PUT /provider is not exposed through /capabilities/%s (reserved)", manifest.id, cap)
                    methods -= {"PUT"}
                if methods:
                    exposed.append((compile_path(f"{prefix}{route.path}")[0], methods))
        _EXPOSED[(manifest.id, cap)] = tuple(exposed)
    for r in manifest.alias_routers:                      # deprecated aliases keep their historical absolute paths
        app.include_router(r)


async def _forward(scope: dict, request: Request) -> Response:
    """Run the request through the app's own router at the plugin-id path (so the route's dependency overrides, scopes and
    validation apply exactly as for /api/v1/plugins/{id}/...) and relay its response: headers verbatim (repeats such as
    several Set-Cookie survive) and the body chunk by chunk, so a streaming route streams."""
    started = asyncio.Event()
    chunks: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=16)      # bounded: a slow client slows the route down
    start: dict = {}

    async def send(message) -> None:
        if message["type"] == "http.response.start":
            start.update(message)
            started.set()
        elif message["type"] == "http.response.body":
            if message.get("body"):
                await chunks.put(message["body"])
            if not message.get("more_body", False):
                await chunks.put(None)

    async def run() -> None:
        try:
            await request.app.router(scope, request.receive, send)
        finally:
            started.set()                                  # a route that raises before responding must not leave us waiting
            await chunks.put(None)

    task = asyncio.create_task(run())
    await started.wait()
    if not start:
        await task                                         # re-raises what the route raised
        return Response(status_code=500)

    async def body():
        try:
            while (chunk := await chunks.get()) is not None:
                yield chunk
            await task
        finally:
            if not task.done():                            # the client went away mid-stream
                task.cancel()

    response = StreamingResponse(body(), status_code=start.get("status", 500))
    response.raw_headers = [(k, v) for k, v in start.get("headers", [])]
    return response


# Any valid key gets in; the inner route then enforces its own scope. Without this, anonymous callers could probe which
# capabilities are active (the route is hidden from OpenAPI, so tests/test_route_auth.py cannot catch the gap).
@router.api_route("/capabilities/{cap}/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False,
                  dependencies=[Depends(require_any_key)])
async def dispatch(cap: str, rest: str, request: Request):
    active = plugin_host.active(cap)
    if active is None:
        return JSONResponse(status_code=409, content={"error": "capability_unavailable", "capability": cap, "feature": None})
    if ".." in rest.split("/"):
        raise HTTPException(status_code=404)
    plugin_id = active.manifest.id
    target = f"/api/v1/plugins/{plugin_id}/{rest}"
    scope = {**request.scope, "path": target, "raw_path": target.encode()}
    path_matches = [methods for regex, methods in _EXPOSED.get((plugin_id, cap), ()) if regex.match(target)]
    if path_matches:
        if not any(request.method in methods for methods in path_matches):
            raise HTTPException(status_code=405)
        return await _forward(scope, request)
    raise HTTPException(status_code=404, detail=f"{plugin_id!r} exposes no {rest!r} for {cap}")
