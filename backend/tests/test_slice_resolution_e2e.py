"""BIZ-291: `resolve_slicing` against the real plugin host and a mocked HTTP sidecar (no fakes of the resolution seams).
Lives outside tests/services: that conftest installs a fake healthy provider for every test."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.services import slice_resolution as sr
from app.services.providers.contract import Action
from tests.services.test_slicing_provider_contract import sidecar  # noqa: F401  (fixture)

pytestmark = pytest.mark.asyncio


async def test_end_to_end_with_the_real_plugin_host_and_a_fake_sidecar(sidecar, monkeypatch):
    """No fakes for resolution: the bundled Laminus plugin, the host's default, a mocked HTTP sidecar."""
    monkeypatch.setenv("LAMINUS_SIDECAR_URL", "http://laminus.test")
    d = await sr.resolve_slicing(SimpleNamespace(name="P1", preferred_slicer_plugin_id=None, bed_x_mm=256.0, bed_y_mm=256.0,
                                                 model_uuid=None, machine_profile="Machine 0.4", profile_reason=None), "m.3mf")
    assert d.outcome.action is Action.PRODUCE and d.provider_id == "laminus"
    sidecar.handler = lambda request: httpx.Response(503)
    d = await sr.resolve_slicing(SimpleNamespace(name="P1", preferred_slicer_plugin_id=None, bed_x_mm=256.0, bed_y_mm=256.0,
                                                 model_uuid=None, machine_profile="Machine 0.4", profile_reason=None), "m.3mf")
    assert d.blocked and d.recoverable and d.message == "Laminus is not ready — slicing paused"
