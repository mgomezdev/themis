"""Configured choose-one slicing through the host, with two in-memory adapters."""
from dataclasses import replace

from pydantic import BaseModel

from app import plugins
from app.plugins.capabilities.gcode_produce import CAPABILITY
from app.services import slice_cache
from app.services.providers.contract import Reason
from app.services.providers.slicing import get_slicing_provider
from app.services.slice_resolution import PrinterView, resolve_slicing
from app.services.slicer_service import SliceRequest, SlicerService
from tests.fake_providers import FakeGcodeProducer, FakeSlicingProvider
from tests.plugins.dummy_plugin import make_manifest


async def test_configured_default_and_printer_preference_use_distinct_cache_keys(host, monkeypatch, tmp_path):
    class Settings(BaseModel):
        pass

    def register(plugin_id):
        producer = FakeGcodeProducer(plugin_id)
        adapter = FakeSlicingProvider()   # Both adapters report the same connection identity.

        class Plugin:
            def __init__(self, settings):
                pass

            def gcode_producer(self):
                return producer

            def slicing_provider(self):
                return adapter

        plugins.register_plugin(make_manifest(plugin_id, cap=CAPABILITY, features=frozenset(),
                                              factory=Plugin, settings_model=Settings, secret_fields=frozenset()))
        return producer, adapter

    default, default_adapter = register("slicer_a")
    preferred, preferred_adapter = register("slicer_b")
    monkeypatch.setattr("app.plugins.host.plugin_host", host)
    await host.start()
    await host.update_config("slicer_a", enabled=True)
    await host.update_config("slicer_b", enabled=True)
    await host.set_provider(CAPABILITY, "slicer_a")
    assert get_slicing_provider() is default_adapter

    req = SliceRequest(job_id=1, source_3mf=str(tmp_path / "part.stl"), plate_number=1, machine_preset="M",
                       process_preset="P", filament_presets=["F"])
    printer = PrinterView("P1", None, 250, 250, "model", machine_profile="M")
    service = SlicerService(str(tmp_path))

    async def slice_for(target):
        decision = await resolve_slicing(target, "part.stl")
        assert not decision.blocked
        request = replace(req, provider_id=decision.provider_id)
        service.slice(request)
        return slice_cache.cache_key(slice_cache.key_inputs(request, "model-hash", None, None))

    first = await slice_for(printer)
    second = await slice_for(replace(printer, preferred_slicer_plugin_id="slicer_b"))
    assert first != second
    assert len(default.calls) == len(preferred.calls) == 1

    await host.set_provider(CAPABILITY, "slicer_b")
    assert get_slicing_provider() is preferred_adapter
    assert await slice_for(printer) == second

    await host.update_config("slicer_b", enabled=False)
    assert get_slicing_provider() is None
    assert slice_cache.key_inputs(req, "model-hash", None, None) is None
    assert (await resolve_slicing(printer, "part.stl")).outcome.reason is Reason.NO_PROVIDER
    dormant = await resolve_slicing(replace(printer, preferred_slicer_plugin_id="slicer_b"), "part.stl")
    assert dormant.outcome.reason is Reason.PREFERENCE_DORMANT
    assert len(default.calls) == 1

    await host.set_provider(CAPABILITY, None)
    assert get_slicing_provider() is None
    assert await slice_for(replace(printer, preferred_slicer_plugin_id="slicer_a")) == first
