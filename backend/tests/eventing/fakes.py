"""Fake plugins for the event tests: a subscriber (configurable misbehaviour) and a definer/publisher of a namespaced event."""
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from app import plugins
from app.eventing import EventDef, EventSubscription
from app.plugins.manifest import HOST_API, PluginManifest
from tests.plugins.dummy_plugin import DummySettings


class Ready(BaseModel):
    item: str


class Subscriber:
    """`mode`: ok | raise (embeds the plugin's token in the message) | hang | slow (0.05 s)."""
    def __init__(self, settings: DummySettings) -> None:
        self.mode, self.token = settings.mode, settings.token
        self.got: list = []

    async def on_event(self, envelope) -> None:
        if self.mode == "raise":
            raise RuntimeError(f"upstream rejected token={self.token} at https://user:hunter2@example.test/x")
        if self.mode == "hang":
            await asyncio.sleep(60)
        if self.mode == "slow":
            await asyncio.sleep(0.05)
        self.got.append(envelope)

    def not_async(self, envelope) -> None:                  # a mis-declared handler
        self.got.append(envelope)


class Definer:
    def __init__(self, settings: DummySettings) -> None:
        self.settings = settings


def subscriber_manifest(plugin_id: str, *events: str, handler: str = "on_event", timeout: float = 10.0, queue_size: int = 1000) -> PluginManifest:
    return PluginManifest(id=plugin_id, name=plugin_id, version="1.0.0", host_api=HOST_API, settings_model=DummySettings,
                          factory=Subscriber, secret_fields=frozenset({"token"}),
                          subscribes=tuple(EventSubscription(e, handler, timeout=timeout, queue_size=queue_size) for e in events))


def definer_manifest(plugin_id: str = "pub_one", *, durability: str = "best_effort") -> PluginManifest:
    return PluginManifest(id=plugin_id, name=plugin_id, version="1.0.0", host_api=HOST_API, settings_model=DummySettings,
                          factory=Definer, defines_events=(EventDef(f"{plugin_id}.ready", durability=durability, payload_model=Ready),))


async def enable(host, plugin_id: str, **settings) -> Subscriber | Definer:
    secrets = {"token": settings.pop("token")} if "token" in settings else None
    await host.update_config(plugin_id, enabled=True, settings=settings or None, secrets=secrets)
    return host._instances[plugin_id]


def register(*manifests: PluginManifest) -> None:
    for m in manifests:
        plugins.register_plugin(m)
