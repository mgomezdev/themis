"""Contract suite for FilamentInventoryProvider, run against the in-memory fake and the Spoolman
adapter (backed by the real tests/spoolman_mock.py app)."""
from __future__ import annotations

import copy
import json

import pytest

from app.models import SpoolmanConfig
from app.services.providers.filament_inventory import (
    Filament,
    InventoryProviderError,
    Spool,
    get_inventory_provider,
)
from app.services.providers.spoolman import SpoolmanInventoryProvider
from tests import spoolman_mock
from tests.fake_providers import FakeInventoryProvider


@pytest.fixture(autouse=True)
def _restore_spoolman_mock():
    saved = (copy.deepcopy(spoolman_mock._FILAMENTS), copy.deepcopy(spoolman_mock._SPOOLS))
    yield
    spoolman_mock._FILAMENTS[:] = saved[0]
    spoolman_mock._SPOOLS[:] = saved[1]


def _fake() -> FakeInventoryProvider:
    return FakeInventoryProvider(
        filaments=[
            Filament(ref="1", name="PLA White", vendor="Elegoo", material="PLA", color_hex="FFFFFF"),
            Filament(ref="2", name="PLA Black", vendor="Elegoo", material="PLA", color_hex="000000"),
        ],
        spools=[
            Spool(ref="1", filament_ref="1", filament_name="PLA White", remaining_weight=800.0),
            Spool(ref="2", filament_ref="2", filament_name="PLA Black", remaining_weight=500.0),
        ],
    )


@pytest.fixture(params=["fake", "spoolman"])
def provider(request, spoolman_upstream):
    if request.param == "fake":
        return _fake()
    return SpoolmanInventoryProvider("http://spoolman.test", "key")


async def test_capabilities_declared(provider):
    assert provider.TRACKS_WEIGHT and provider.RECORDS_USAGE and provider.PROFILE_BINDINGS


async def test_test_connection_returns_info_dict(provider):
    info = await provider.test_connection()
    assert isinstance(info, dict) and info


async def test_list_filaments_maps_fields(provider):
    filaments = await provider.list_filaments()
    white = next(f for f in filaments if f.ref == "1")
    assert (white.name, white.vendor, white.material, white.color_hex) == ("PLA White", "Elegoo", "PLA", "FFFFFF")
    assert all(isinstance(f.ref, str) for f in filaments)


async def test_get_filament_by_ref(provider):
    assert (await provider.get_filament("2")).name == "PLA Black"


async def test_get_missing_filament_raises_neutral_error(provider):
    with pytest.raises(InventoryProviderError) as ei:
        await provider.get_filament("999")
    assert ei.value.status == 404 and ei.value.code == "404"


async def test_list_spools_maps_fields(provider):
    spools = await provider.list_spools()
    s2 = next(s for s in spools if s.ref == "2")
    assert (s2.filament_ref, s2.filament_name, s2.remaining_weight) == ("2", "PLA Black", 500.0)


async def test_record_usage_reduces_remaining_weight(provider):
    await provider.record_usage("1", 50.0)
    s1 = next(s for s in await provider.list_spools() if s.ref == "1")
    assert s1.remaining_weight == 750.0


async def test_record_usage_unknown_spool_raises(provider):
    with pytest.raises(InventoryProviderError):
        await provider.record_usage("999", 1.0)


async def test_profile_bindings_round_trip(provider):
    assert await provider.get_profile_bindings("1") == {}
    updated = await provider.set_profile_bindings("1", {"Printer A": ["PLA @A", "PLA+ @A"]})
    assert updated.ref == "1"
    assert await provider.get_profile_bindings("1") == {"Printer A": ["PLA @A", "PLA+ @A"]}
    assert (await provider.get_filament("1")).profile_bindings == {"Printer A": ["PLA @A", "PLA+ @A"]}


# ---- Spoolman-adapter specifics: wire format and error mapping ----

async def test_spoolman_bindings_are_double_json_encoded_on_the_wire(spoolman_upstream):
    p = SpoolmanInventoryProvider("http://spoolman.test", "key")
    await p.set_profile_bindings("1", {"Printer A": ["PLA @A"]})
    stored = spoolman_mock._FILAMENTS[0]["extra"]["orca_profiles"]
    assert json.loads(json.loads(stored)) == {"Printer A": ["PLA @A"]}
    patch_req = next(r for r in spoolman_upstream.requests if r.method == "PATCH")
    assert patch_req.headers["X-API-Key"] == "key"


async def test_spoolman_malformed_bindings_read_as_empty(spoolman_upstream):
    spoolman_mock._FILAMENTS[0]["extra"]["orca_profiles"] = "not json"
    p = SpoolmanInventoryProvider("http://spoolman.test")
    assert await p.get_profile_bindings("1") == {}
    spoolman_mock._FILAMENTS[0]["extra"]["orca_profiles"] = json.dumps(json.dumps(["not", "a", "dict"]))
    assert await p.get_profile_bindings("1") == {}


async def test_spoolman_raw_payload_preserved_for_legacy_shape(spoolman_upstream):
    p = SpoolmanInventoryProvider("http://spoolman.test")
    assert [f.raw for f in await p.list_filaments()] == spoolman_mock._FILAMENTS
    assert [s.raw for s in await p.list_spools()] == spoolman_mock._SPOOLS


async def test_spoolman_transport_error_maps_to_neutral_error(spoolman_upstream):
    import httpx

    def boom(request):
        raise httpx.ConnectError("refused")

    spoolman_upstream.handler = boom
    with pytest.raises(InventoryProviderError) as ei:
        await SpoolmanInventoryProvider("http://spoolman.test").list_spools()
    assert ei.value.code == "ConnectError" and ei.value.status is None


async def test_spoolman_http_status_maps_code_and_status(spoolman_upstream):
    import httpx
    spoolman_upstream.handler = lambda request: httpx.Response(502, text="bad gateway")
    with pytest.raises(InventoryProviderError) as ei:
        await SpoolmanInventoryProvider("http://spoolman.test").test_connection()
    assert (ei.value.code, ei.value.status) == ("502", 502)


# ---- accessor ----

async def test_accessor_none_when_missing_disabled_or_no_url(session_factory):
    async with session_factory() as s:
        assert await get_inventory_provider(s) is None
        s.add(SpoolmanConfig(id=1, enabled=False, url="http://x"))
        await s.commit()
        assert await get_inventory_provider(s) is None
        row = await s.get(SpoolmanConfig, 1)
        row.enabled, row.url = True, None
        await s.commit()
        assert await get_inventory_provider(s) is None


async def test_accessor_returns_spoolman_adapter_when_configured(session_factory):
    async with session_factory() as s:
        s.add(SpoolmanConfig(id=1, enabled=True, url="http://x", api_key="k"))
        await s.commit()
        p = await get_inventory_provider(s)
    assert isinstance(p, SpoolmanInventoryProvider)
