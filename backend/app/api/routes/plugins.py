"""Plugin management API (BIZ-202 §3.9): list manifests, read/write a plugin's settings (secrets write-only), test a
connection, choose the provider of a kind. Gated on `settings:read` / `settings:write`."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...auth import require_scope
from ...plugins import PluginError, get_plugin, registered_plugins
from ...plugins.host import plugin_host
from ...plugins.manifest import PluginManifest

router = APIRouter(prefix="/api/v1", tags=["plugins"])


def _ui(m: PluginManifest) -> dict:
    u = m.ui
    return {"mode": u.mode, "nav_label": u.nav_label or m.name, "nav_placement": u.nav_placement, "nav_icon": u.nav_icon,
            "tabs": [{"id": t.id, "label": t.label, "renderer": t.renderer} for t in u.tabs]}


def _summary(m: PluginManifest) -> dict:
    return {
        "id": m.id, "name": m.name, "kind": m.kind, "version": m.version, "description": m.description,
        "docs_url": m.docs_url, "source": "bundled", "capabilities": sorted(m.capabilities), "ui": _ui(m),
        "enabled": plugin_host.is_enabled(m.id),
        "active": plugin_host.active(m.kind) is not None and plugin_host.slot(m.kind) == m.id,
        "error": plugin_host.build_error(m.id) or plugin_host.state(m.id).get("last_error"),
    }


def _detail(m: PluginManifest) -> dict:
    stored = plugin_host.settings(m.id)
    defaults = {k: f.default for k, f in m.settings_model.model_fields.items()
                if k not in m.secret_fields and not f.is_required()}
    return {
        **_summary(m),
        "settings": {**defaults, **{k: v for k, v in stored.items() if k not in m.secret_fields}},
        "secrets": {f: plugin_host.has_secret(m.id, f) for f in sorted(m.secret_fields)},     # set / not set, never the value
        "settings_schema": m.settings_model.model_json_schema(),
        "secret_fields": sorted(m.secret_fields),
        "state": plugin_host.state(m.id),
    }


def _get_or_404(plugin_id: str) -> PluginManifest:
    m = get_plugin(plugin_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"Unknown plugin {plugin_id!r}")
    return m


@router.get("/plugins", summary="List plugins (manifests, UI contributions, capabilities, enabled/active)",
            dependencies=[Depends(require_scope("settings:read"))])
async def list_plugins():
    kinds = sorted({m.kind for m in registered_plugins()})
    return {"plugins": [_summary(m) for m in registered_plugins()], "slots": {k: plugin_host.slot(k) for k in kinds}}


@router.get("/plugins/{plugin_id}", summary="One plugin: settings (secrets as set/unset), schema and state",
            dependencies=[Depends(require_scope("settings:read"))])
async def get_plugin_detail(plugin_id: str):
    return _detail(_get_or_404(plugin_id))


class PluginUpdate(BaseModel):
    enabled: bool | None = None
    settings: dict | None = None
    secrets: dict[str, str] | None = None        # write-only; omit a key to keep it, "" to clear it


@router.put("/plugins/{plugin_id}", summary="Update a plugin's settings / enabled flag",
            dependencies=[Depends(require_scope("settings:write"))])
async def update_plugin(plugin_id: str, body: PluginUpdate):
    m = _get_or_404(plugin_id)
    try:
        await plugin_host.update_config(plugin_id, enabled=body.enabled, settings=body.settings, secrets=body.secrets)
    except PluginError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _detail(m)


class PluginTest(BaseModel):
    settings: dict | None = None
    secrets: dict[str, str] | None = None


@router.post("/plugins/{plugin_id}/test", summary="Test a connection with the saved or the supplied (unsaved) settings",
             dependencies=[Depends(require_scope("settings:write"))])
async def test_plugin(plugin_id: str, body: PluginTest):
    _get_or_404(plugin_id)
    try:
        instance = plugin_host.build_candidate(plugin_id, body.settings, body.secrets)
        info = await instance.test_connection()
    except PluginError as e:
        raise HTTPException(status_code=422, detail=plugin_host.redact(plugin_id, str(e), tuple((body.secrets or {}).values())))
    except Exception as e:
        return {"ok": False, "message": plugin_host.redact(plugin_id, str(e), tuple((body.secrets or {}).values()))}
    return {"ok": True, **({"version": info.get("version")} if isinstance(info, dict) else {})}


class SlotBody(BaseModel):
    plugin_id: str | None = None


@router.put("/extension-slots/{kind}", summary="Choose the provider for a plugin kind (null = none)",
            dependencies=[Depends(require_scope("settings:write"))])
async def set_slot(kind: str, body: SlotBody):
    try:
        await plugin_host.set_slot(kind, body.plugin_id)
    except PluginError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"kind": kind, "plugin_id": plugin_host.slot(kind)}
