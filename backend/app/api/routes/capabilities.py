"""Capability management API: every known capability (core + plugin-defined), its providers and the selected one."""
from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.routing import Match

from ...auth import require_scope
from ...plugins import PluginError, capability_catalog, definer_of, providers_of, registered_plugins
from ...plugins.capabilities.definition import CapabilityDef
from ...plugins.host import plugin_host
from ...plugins.manifest import PluginManifest

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

# (plugin id, capability) -> the full path templates its `Provide.routers` expose. The dispatcher runs ONLY these.
_EXPOSED: dict[tuple[str, str], frozenset[str]] = {}


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
        _EXPOSED[(manifest.id, cap)] = frozenset(f"{prefix}{route.path}" for r in provide.routers for route in r.routes)
    for r in manifest.alias_routers:                      # deprecated aliases keep their historical absolute paths
        app.include_router(r)


async def _run(route, scope: dict, request: Request) -> Response:
    """Run an already-mounted route (so its dependency overrides, scopes and validation apply) and capture its response."""
    start: dict = {}
    body = bytearray()

    async def send(message) -> None:
        if message["type"] == "http.response.start":
            start.update(message)
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    await route.handle(scope, request.receive, send)
    headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in start.get("headers", []) if k.lower() != b"content-length"}
    return Response(content=bytes(body), status_code=start.get("status", 500), headers=headers)


@router.api_route("/capabilities/{cap}/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
async def dispatch(cap: str, rest: str, request: Request):
    active = plugin_host.active(cap)
    if active is None:
        return JSONResponse(status_code=409, content={"error": "capability_unavailable", "capability": cap})
    if ".." in rest.split("/"):
        raise HTTPException(status_code=404)
    plugin_id = active.manifest.id
    target = f"/api/v1/plugins/{plugin_id}/{rest}"
    scope = {**request.scope, "path": target, "raw_path": target.encode()}
    exposed, partial = _EXPOSED.get((plugin_id, cap), frozenset()), None
    for route in request.app.router.routes:
        if getattr(route, "path", None) not in exposed:
            continue
        match, child = route.matches(scope)
        if match is Match.FULL:
            return await _run(route, {**scope, **child}, request)
        if match is Match.PARTIAL:
            partial = route
    if partial is not None:
        raise HTTPException(status_code=405)
    raise HTTPException(status_code=404, detail=f"{plugin_id!r} exposes no {rest!r} for {cap}")
