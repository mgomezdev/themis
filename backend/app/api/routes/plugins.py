"""Plugin management API (BIZ-202 §3.9): list manifests, read/write a plugin's settings (secrets write-only), test a
connection, see capabilities.py for choosing providers. Gated on `settings:read` / `settings:write`."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import InstalledPlugin
from ...plugins import PluginError, get_plugin, registered_plugins
from ...plugins.host import plugin_host
from ...plugins.manifest import PluginManifest

router = APIRouter(prefix="/api/v1", tags=["plugins"])


def _ui(m: PluginManifest) -> dict:
    u = m.ui
    return {"mode": u.mode, "nav_label": u.nav_label or m.name, "nav_placement": u.nav_placement, "nav_icon": u.nav_icon,
            "tabs": [{"id": t.id, "label": t.label, "renderer": t.renderer} for t in u.tabs]}


def install_info(row: InstalledPlugin | None) -> dict | None:
    """What the installer knows about a non-bundled plugin (None for bundled ones)."""
    if row is None:
        return None
    return {"status": row.status, "version": row.version, "previous_version": row.previous_version, "publisher": row.publisher,
            "source_url": row.source_url, "ref": row.ref, "commit_sha": row.commit_sha, "archive_sha256": row.archive_sha256,
            "installed_at": row.installed_at, "error": row.error, "can_rollback": bool(row.previous_version),
            "can_check_updates": row.source == "github"}


def _provides(m: PluginManifest) -> list[dict]:
    out = []
    for cap, p in sorted(m.provides.items()):
        st = plugin_host.status(cap)
        mine = st.plugin_id == m.id and st.state != "dormant"
        out.append({"capability": cap, "version": p.version, "features": sorted(p.features), "selected": mine,
                    "status": st.state if mine else "not_selected", "waiting_on": list(st.waiting_on) if mine else []})
    return out


def _refs(reqs) -> list[dict]:
    return [{"capability": r.capability, "min_version": r.min_version} for r in reqs]


def _summary(m: PluginManifest, row: InstalledPlugin | None = None) -> dict:
    provides = _provides(m)
    return {
        "id": m.id, "name": m.name, "version": m.version, "description": m.description,
        "docs_url": m.docs_url, "source": row.source if row else "bundled", "loaded": True, "install": install_info(row),
        "provides": provides, "requires": _refs(m.requires), "optional": _refs(m.optional),
        "defines": [d.id for d in m.defines], "ui": _ui(m),
        "enabled": plugin_host.is_enabled(m.id),
        "active": any(x["status"] == "serving" for x in provides),
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
async def list_plugins(session: AsyncSession = Depends(get_session)):
    rows = {r.plugin_id: r for r in (await session.execute(select(InstalledPlugin))).scalars()}
    loaded = registered_plugins()
    plugins = [_summary(m, rows.get(m.id)) for m in loaded]
    # Installed packages that are not running: staged for the next restart, or failed to load (shown with their error).
    for r in sorted(rows.values(), key=lambda r: r.plugin_id):
        if get_plugin(r.plugin_id) is None:
            plugins.append({"id": r.plugin_id, "name": r.name or r.plugin_id, "version": r.version,
                            "description": "", "docs_url": None, "source": r.source, "loaded": False,
                            "install": install_info(r), "provides": [], "requires": [], "optional": [], "defines": [],
                            "ui": {"mode": "section", "nav_label": r.name or r.plugin_id, "nav_placement": "settings",
                                   "nav_icon": None, "tabs": []},
                            "enabled": False, "active": False, "error": r.error})
    pending = [{"plugin_id": r.plugin_id, "name": r.name or r.plugin_id, "version": r.version,
                "change": "uninstall" if r.status == "pending_removal" else ("install" if not r.previous_version else "update")}
               for r in sorted(rows.values(), key=lambda r: r.plugin_id) if r.status in ("pending_restart", "pending_removal")]
    return {"plugins": plugins, "selections": plugin_host.selections(), "pending": pending}


@router.get("/plugins/{plugin_id}", summary="One plugin: settings (secrets as set/unset), schema and state",
            dependencies=[Depends(require_scope("settings:read"))])
async def get_plugin_detail(plugin_id: str):
    return _detail(_get_or_404(plugin_id))


@router.get("/plugins/{plugin_id}/ui/{tab_id}", summary="The schema a `schema` tab is rendered from",
            responses={404: {"description": "Unknown plugin, or the tab is not a schema tab"}},
            dependencies=[Depends(require_scope("settings:read"))])
async def get_tab_schema(plugin_id: str, tab_id: str):
    m = _get_or_404(plugin_id)
    tab = next((t for t in m.ui.tabs if t.id == tab_id and t.renderer == "schema"), None)
    schema = m.ui_schema(tab_id) if tab is not None and m.ui_schema is not None else None
    if schema is None:
        raise HTTPException(status_code=404, detail=f"{plugin_id!r} has no schema tab {tab_id!r}")
    return schema


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
