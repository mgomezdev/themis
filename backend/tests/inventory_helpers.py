"""Helpers for tests that need an active inventory provider (the plugin host is configured per test by `session_factory`)."""
from __future__ import annotations

from app import plugins
from app.plugins.host import plugin_host
from app.plugins.capabilities.filament_inventory import CAPABILITY, InvMaterial, InvSpool
from app.plugins.manifest import HOST_API, PluginManifest, Provide
from pydantic import BaseModel


async def enable_spoolman(url: str = "http://spoolman.test", api_key: str | None = None, **settings) -> None:
    """Select + enable the bundled Spoolman plugin (what `PUT /settings/spoolman {enabled: true, url}` does)."""
    await plugin_host.update_config("spoolman", settings={"url": url, **settings},
                                    secrets={"api_key": api_key} if api_key else None)
    await plugin_host.set_provider(CAPABILITY, "spoolman")


class _NoSettings(BaseModel):
    pass


async def use_provider(provider, plugin_id: str = "spoolman") -> None:
    """Make `provider` (e.g. a FakeInventoryProvider) the active inventory provider."""
    manifest = PluginManifest(id=plugin_id, name="Fake inventory", version="0", host_api=HOST_API,
                              settings_model=_NoSettings, factory=lambda _s: provider,
                              provides={CAPABILITY: Provide(features=frozenset(provider.capabilities))})
    plugins._REGISTRY.pop(plugin_id, None)               # a test may swap the provider more than once
    plugins.register_plugin(manifest)
    plugin_host._fingerprints.pop(plugin_id, None)       # force a rebuild even if this id already has a live instance
    await plugin_host.set_provider(CAPABILITY, plugin_id)


def spool(ref: str, remaining_g: float | None = None, name: str = "", material: str | None = None, vendor: str | None = None,
          material_ref: str | None = "1", location: str | None = None, archived: bool = False, **kw) -> InvSpool:
    mat = InvMaterial(ref=material_ref, name=name, material=material, vendor=vendor) if material_ref is not None else None
    label = " ".join(p for p in (vendor, name) if p) or f"spool {ref}"
    return InvSpool(ref=ref, material_ref=material_ref, material=mat, remaining_g=remaining_g, location=location,
                    label=label, archived=archived, **kw)
