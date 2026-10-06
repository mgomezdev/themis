"""Plugin installation API (BIZ-223, spec §3.11) and the admin restart. Admin interactive session only (API keys are
refused); every install/upgrade/rollback/uninstall/restart is audit-logged. Nothing takes effect until the admin restarts."""
from __future__ import annotations

import asyncio
import logging
import os
import signal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_admin_session
from ...database import get_session
from ...models import ApiKey, InstalledPlugin, Job, Printer
from ...plugins import PluginError, get_plugin
from ...plugins import installer, migrations as plugin_migrations
from ...plugins.host import plugin_host
from ...services import audit
from .plugins import install_info

logger = logging.getLogger("app")
router = APIRouter(prefix="/api/v1", tags=["plugin-install"])
_admin = Depends(require_admin_session("settings:write"))


def _fail(e: PluginError) -> HTTPException:
    return HTTPException(status_code=422 if not isinstance(e, installer.InstallError) else 400, detail=str(e))


async def _chunks(f: UploadFile):
    while chunk := await f.read(64 * 1024):
        yield chunk


def _result(row: InstalledPlugin) -> dict:
    return {"id": row.plugin_id, "name": row.name, "version": row.version, "status": row.status,
            "install": install_info(row), "restart_required": True}


@router.post("/plugins/install", summary="Install a plugin from an uploaded .zip/.tar.gz/.tgz (preview=true stages only)")
async def install_upload(preview: bool = False, file: UploadFile = File(...), key: ApiKey | None = _admin,
                         session: AsyncSession = Depends(get_session)):
    try:
        staged = await installer.stage_upload(_chunks(file), file.filename or "upload")
        if preview:
            return {"preview": staged.preview()}
        return _result(await installer.commit(session, staged.token, actor=audit.actor_of(key)))
    except PluginError as e:
        raise _fail(e)


class GithubBody(BaseModel):
    repo_url: str
    ref: str | None = None
    subdir: str | None = None
    preview: bool = False


@router.post("/plugins/install-from-github", summary="Install a plugin from a public GitHub repo (ref resolved to a commit)")
async def install_github(body: GithubBody, key: ApiKey | None = _admin, session: AsyncSession = Depends(get_session)):
    try:
        staged = await installer.stage_github(body.repo_url, body.ref or None, body.subdir or None)
        if body.preview:
            return {"preview": staged.preview()}
        return _result(await installer.commit(session, staged.token, actor=audit.actor_of(key)))
    except PluginError as e:
        raise _fail(e)


@router.post("/plugins/install/{token}/commit", summary="Commit a previewed (staged) install or upgrade")
async def commit_staged(token: str, key: ApiKey | None = _admin, session: AsyncSession = Depends(get_session)):
    try:
        return _result(await installer.commit(session, token, actor=audit.actor_of(key)))
    except PluginError as e:
        raise _fail(e)


@router.delete("/plugins/install/{token}", status_code=204, summary="Discard a previewed (staged) install",
               dependencies=[_admin])
async def discard_staged(token: str):
    try:
        installer.discard(token)
    except PluginError as e:
        raise _fail(e)


@router.get("/plugins/{plugin_id}/updates", summary="Check a GitHub-installed plugin for a newer commit",
            dependencies=[_admin])
async def check_updates(plugin_id: str, session: AsyncSession = Depends(get_session)):
    try:
        return await installer.check_update(await installer.get_row(session, plugin_id))
    except PluginError as e:
        raise _fail(e)


class UpgradeBody(BaseModel):
    preview: bool = False


@router.post("/plugins/{plugin_id}/upgrade", summary="Upgrade a GitHub-installed plugin to the latest commit of its ref")
async def upgrade(plugin_id: str, body: UpgradeBody | None = None, key: ApiKey | None = _admin,
                  session: AsyncSession = Depends(get_session)):
    body = body or UpgradeBody()
    try:
        row = await installer.get_row(session, plugin_id)
        if row.source != "github" or not row.source_url:
            raise installer.InstallError("only GitHub-installed plugins can be upgraded in place; upload the newer version")
        staged = await installer.stage_github(row.source_url, row.ref, row.subdir)
        if staged.toml.id != plugin_id:
            installer.discard(staged.token)
            raise installer.InstallError(f"the repository now provides plugin {staged.toml.id!r}, not {plugin_id!r}")
        if body.preview:
            return {"preview": staged.preview()}
        return _result(await installer.commit(session, staged.token, actor=audit.actor_of(key), expect_id=plugin_id))
    except PluginError as e:
        raise _fail(e)


@router.post("/plugins/{plugin_id}/rollback", summary="Go back to the previous version at the next restart")
async def rollback(plugin_id: str, key: ApiKey | None = _admin, session: AsyncSession = Depends(get_session)):
    try:
        return _result(await installer.rollback(session, plugin_id, actor=audit.actor_of(key)))
    except PluginError as e:
        raise _fail(e)


async def _remove_data(plugin_id: str) -> None:
    """Drop the plugin's own tables (its migrations' down(), newest first), its settings row and its provider slot."""
    from ...models import ExtensionSlot, PluginConfig
    manifest = get_plugin(plugin_id)
    sf = plugin_host.session_factory
    assert sf is not None
    if manifest is not None:
        async with sf() as s:
            conn = await s.connection()
            while await plugin_migrations.rollback_plugin_migration(conn, manifest) is not None:
                pass
            await s.commit()
    async with sf() as s:
        cfg = await s.get(PluginConfig, plugin_id)
        if cfg is not None:
            await s.delete(cfg)
        for slot in (await s.execute(select(ExtensionSlot).where(ExtensionSlot.plugin_id == plugin_id))).scalars():
            slot.plugin_id = None
        await s.commit()
    await plugin_host.reload()


@router.delete("/plugins/{plugin_id}", summary="Uninstall at the next restart (data is kept unless remove_data=true)")
async def uninstall(plugin_id: str, remove_data: bool = False, key: ApiKey | None = _admin,
                    session: AsyncSession = Depends(get_session)):
    if get_plugin(plugin_id) is not None and await session.get(InstalledPlugin, plugin_id) is None:
        raise HTTPException(status_code=409, detail="Bundled plugins can be disabled, not uninstalled")
    try:
        await installer.get_row(session, plugin_id)
        if get_plugin(plugin_id) is not None:
            await plugin_host.update_config(plugin_id, enabled=False)      # stop it now; the code goes at the restart
        if remove_data:
            await _remove_data(plugin_id)
        return _result(await installer.mark_uninstall(session, plugin_id, actor=audit.actor_of(key), removed_data=remove_data))
    except PluginError as e:
        raise _fail(e)


# --- restart ---------------------------------------------------------------------------------------------------------

def exit_process() -> None:                      # tests replace this: it ends the process (Docker's restart policy revives it)
    os.kill(os.getpid(), signal.SIGTERM)


async def _printing(session: AsyncSession) -> list[str]:
    rows = (await session.execute(select(Printer.name).join(Job, Job.assigned_printer_id == Printer.id)
                                  .where(Job.status == "printing"))).scalars().all()
    return sorted(set(rows))


@router.get("/system/restart", summary="What a restart would apply, and whether anything is printing", dependencies=[_admin])
async def restart_status(session: AsyncSession = Depends(get_session)):
    pending = (await session.execute(select(InstalledPlugin).where(
        InstalledPlugin.status.in_(("pending_restart", "pending_removal"))))).scalars().all()
    return {"pending": [{"plugin_id": r.plugin_id, "version": r.version, "status": r.status} for r in pending],
            "printing": await _printing(session)}


class RestartBody(BaseModel):
    force: bool = False


@router.post("/system/restart", summary="Restart Themis (applies every pending plugin change); warns if printing")
async def restart(body: RestartBody | None = None, key: ApiKey | None = _admin, session: AsyncSession = Depends(get_session)):
    printing = await _printing(session)
    if printing and not (body and body.force):
        raise HTTPException(status_code=409, detail={"error": "printing", "printers": printing})
    await audit.record(session, audit.actor_of(key), "system.restart", None, {"printing": printing})
    await session.commit()
    asyncio.get_running_loop().call_later(0.5, exit_process)           # after this response is sent
    return {"restarting": True}
