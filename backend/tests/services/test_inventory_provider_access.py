import pytest

from app.plugins.capabilities.filament_inventory import CAPABILITY, TRACKS_WEIGHT
from app.services.inventory import provider
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import use_provider


async def test_has_and_require_go_through_the_capability(session_factory):
    await use_provider(FakeInventoryProvider(capabilities=frozenset({TRACKS_WEIGHT})))
    assert provider.has(TRACKS_WEIGHT) and not provider.has("WRITE_WEIGHT")
    assert provider.require(TRACKS_WEIGHT) == "spoolman"
    with pytest.raises(provider.CapabilityUnavailable) as e:
        provider.require("WRITE_WEIGHT")
    assert (e.value.capability_id, e.value.feature) == (CAPABILITY, "WRITE_WEIGHT")


async def test_no_provider_raises_with_the_capability_id(session_factory):
    with pytest.raises(provider.CapabilityUnavailable) as e:
        provider.require()
    assert (e.value.capability_id, e.value.feature) == ("inventory.filament", None)
    assert provider.active_provider() is None and provider.provider_id() is None


async def test_active_provider_is_the_serving_part(session_factory):
    fake = FakeInventoryProvider()
    await use_provider(fake)
    assert provider.active_provider() is fake
