"""Capability management API: every known capability (core + plugin-defined), its providers and the selected one."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...auth import require_scope
from ...plugins import PluginError, capability_catalog, definer_of, providers_of, registered_plugins
from ...plugins.capabilities.definition import CapabilityDef
from ...plugins.host import plugin_host

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
