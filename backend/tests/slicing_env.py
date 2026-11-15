"""Test seam for the queue's slicing provider (BIZ-291): install a `SlicingProvider` (or None = unconfigured) as THE enabled,
default `gcode.produce` provider, without a live plugin host. Resolution runs the real `LaminusGcodeProducer` over it, so a
provider whose `health` raises reads as not-ready / unreachable exactly as in production."""
from __future__ import annotations

import contextlib
from unittest.mock import patch

from app.services.providers.laminus.producer import LaminusGcodeProducer

PLUGIN_ID = "laminus"


@contextlib.contextmanager
def slicing_provider(provider, plugin_id: str = PLUGIN_ID):
    producer = LaminusGcodeProducer(provider, version="test") if provider is not None else None
    with contextlib.ExitStack() as stack:
        for target, value in (
            ("app.services.slice_resolution.enabled_provider_ids", lambda: [plugin_id]),
            ("app.services.slice_resolution.default_provider_id", lambda: plugin_id),
            ("app.services.slicer_profiles.effective_provider_id", lambda preferred: preferred or plugin_id),
            ("app.services.slice_resolution.gcode_producer_for", lambda pid: producer),
            ("app.services.queue_engine.slicing_provider_for", lambda pid: provider),
            ("app.services.slicer_service.gcode_producer_for", lambda pid: producer),
            ("app.services.slicer_service.default_provider_id", lambda: plugin_id),
            ("app.services.slice_saver.slicing_provider_for", lambda pid: provider),
        ):
            stack.enter_context(patch(target, value))
        yield provider


@contextlib.contextmanager
def slicing_providers(providers: dict, default: str | None):
    """Several enabled `gcode.produce` providers: `{plugin_id: SlicingProvider | None}` (None = enabled but unconfigured)."""
    producers = {pid: (LaminusGcodeProducer(p, version="test") if p is not None else None) for pid, p in providers.items()}
    with contextlib.ExitStack() as stack:
        for target, value in (
            ("app.services.slice_resolution.enabled_provider_ids", lambda: list(providers)),
            ("app.services.slice_resolution.default_provider_id", lambda: default),
            ("app.services.slicer_profiles.effective_provider_id", lambda preferred: preferred or default),
            ("app.services.slice_resolution.gcode_producer_for", lambda pid: producers.get(pid)),
            ("app.services.queue_engine.slicing_provider_for", lambda pid: providers.get(pid)),
            ("app.services.slicer_service.gcode_producer_for", lambda pid: producers.get(pid)),
            ("app.services.slicer_service.default_provider_id", lambda: default),
            ("app.services.slice_saver.slicing_provider_for", lambda pid: providers.get(pid)),
        ):
            stack.enter_context(patch(target, value))
        yield providers
