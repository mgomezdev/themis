"""A throwaway plugin of a made-up kind, so the kind-agnostic host is tested without any real provider."""
from __future__ import annotations

import asyncio
import types

from pydantic import BaseModel

from app.plugins import PluginManifest, UiContribution, UiTab
from app.plugins.manifest import HOST_API


class DummySettings(BaseModel):
    url: str = "http://dummy.test"
    token: str | None = None
    mode: str = "ok"                       # ok | raise | hang | bad-config


class DummyProvider:
    instances: list["DummyProvider"] = []

    def __init__(self, settings: DummySettings) -> None:
        if settings.mode == "bad-config":
            raise ValueError("cannot build with this config")
        self.settings, self.closed, self.calls = settings, False, 0
        DummyProvider.instances.append(self)

    async def ping(self, value="pong"):
        self.calls += 1
        if self.settings.mode == "raise":
            raise RuntimeError("provider exploded")
        if self.settings.mode == "hang":
            await asyncio.sleep(60)
        return value

    async def aclose(self) -> None:
        self.closed = True


def migration(version: int, sql_up: str, sql_down: str, name: str = "m"):
    mod = types.ModuleType(f"dummy_mig_v{version}")
    mod.version, mod.name = version, name

    async def up(conn):
        from sqlalchemy import text
        await conn.execute(text(sql_up))

    async def down(conn):
        from sqlalchemy import text
        await conn.execute(text(sql_down))

    mod.up, mod.down = up, down
    return mod


def make_manifest(plugin_id: str = "dummy_one", kind: str = "dummy_kind", capabilities=frozenset({"PING"}), **over) -> PluginManifest:
    fields = dict(id=plugin_id, name="Dummy", kind=kind, version="1.0.0", host_api=HOST_API,
                  settings_model=DummySettings, factory=DummyProvider, capabilities=capabilities,
                  secret_fields=frozenset({"token"}),
                  ui=UiContribution(mode="page", nav_label="Dummy", tabs=(UiTab("main", "Main"),)))
    fields.update(over)
    return PluginManifest(**fields)
