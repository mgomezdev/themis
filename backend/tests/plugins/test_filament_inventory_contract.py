"""Contract suite for every `filament_inventory` provider (spec §6): a new provider has to pass it.

Parametrized over the in-memory fake (a weight-tracking provider WITHOUT label scanning) and the Spoolman plugin (backed
by the real tests/spoolman_mock.py app). Local inventory joins the parameter list in its own phase."""
from __future__ import annotations

import copy
import json

import httpx
import pytest

from app.plugins.kinds.filament_inventory import (
    ALL_CAPABILITIES, LABEL_SCAN, PROFILE_LINKS_READ, PROFILE_LINKS_WRITE, TRACKS_WEIGHT, WRITE_WEIGHT,
    FilamentInventoryProvider, InvMaterial, InvSpool, InventoryProviderError, NotSupported,
)
from app.plugins.spoolman import MANIFEST as SPOOLMAN_MANIFEST
from app.plugins.spoolman.provider import SpoolmanProvider
from app.plugins.spoolman.settings import SpoolmanSettings
from tests import spoolman_mock
from tests.fake_providers import FakeInventoryProvider


@pytest.fixture(autouse=True)
def _restore_spoolman_mock():
    saved = (copy.deepcopy(spoolman_mock._FILAMENTS), copy.deepcopy(spoolman_mock._SPOOLS))
    yield
    spoolman_mock._FILAMENTS[:] = saved[0]
    spoolman_mock._SPOOLS[:] = saved[1]


def _fake() -> FakeInventoryProvider:
    white = InvMaterial(ref="1", name="PLA White", vendor="Elegoo", material="PLA", color_hex="#FFFFFF", profile_links={})
    black = InvMaterial(ref="2", name="PLA Black", vendor="Elegoo", material="PLA", color_hex="#000000", profile_links={})
    return FakeInventoryProvider(
        materials=[white, black],
        spools=[InvSpool(ref="1", material_ref="1", material=white, remaining_g=800.0, label="Elegoo PLA White"),
                InvSpool(ref="2", material_ref="2", material=black, remaining_g=500.0, label="Elegoo PLA Black")])


def _spoolman() -> SpoolmanProvider:
    return SpoolmanProvider(SpoolmanSettings(url="http://spoolman.test", api_key="key"))


@pytest.fixture(params=["fake", "spoolman"])
def provider(request, spoolman_upstream) -> FilamentInventoryProvider:
    return _fake() if request.param == "fake" else _spoolman()


# --- declaration ---------------------------------------------------------------------------------------------------

async def test_capabilities_are_a_known_subset_and_the_bundled_manifest_declares_the_same(provider):
    assert provider.capabilities <= ALL_CAPABILITIES
    if isinstance(provider, SpoolmanProvider):
        assert SPOOLMAN_MANIFEST.capabilities == provider.capabilities


async def test_test_connection_returns_an_info_dict(provider):
    info = await provider.test_connection()
    assert isinstance(info, dict) and info


# --- reads ---------------------------------------------------------------------------------------------------------

async def test_refs_are_strings_and_materials_are_complete(provider):
    materials = await provider.list_materials()
    assert materials and all(isinstance(m.ref, str) and isinstance(m, InvMaterial) for m in materials)
    white = next(m for m in materials if m.ref == "1")
    assert (white.name, white.vendor, white.material, white.color_hex) == ("PLA White", "Elegoo", "PLA", "#FFFFFF")


async def test_spools_reference_their_material_and_carry_a_label(provider):
    spools = await provider.list_spools()
    materials = {m.ref for m in await provider.list_materials()}
    assert spools and all(isinstance(s.ref, str) and s.label for s in spools)
    assert all(s.material_ref in materials for s in spools)
    s2 = next(s for s in spools if s.ref == "2")
    assert (s2.material.name, s2.remaining_g) == ("PLA Black", 500.0)


async def test_a_provider_that_does_not_track_weight_reports_none_and_one_that_does_reports_numbers(provider):
    for s in await provider.list_spools():
        if TRACKS_WEIGHT in provider.capabilities:
            assert isinstance(s.remaining_g, (int, float))
        else:
            assert s.remaining_g is None


async def test_get_spool_returns_the_spool_or_none(provider):
    assert (await provider.get_spool("2")).material.name == "PLA Black"
    assert await provider.get_spool("999") is None


# --- optional operations: work with the capability, refuse without it ---------------------------------------------------

async def test_set_remaining_round_trips_and_is_idempotent_or_is_refused(provider):
    if WRITE_WEIGHT not in provider.capabilities:
        with pytest.raises(NotSupported):
            await provider.set_remaining("1", 700.0)
        return
    await provider.set_remaining("1", 700.0)
    assert (await provider.get_spool("1")).remaining_g == 700.0              # set, then read back exactly
    await provider.set_remaining("1", 700.0)
    await provider.set_remaining("1", 700.0)
    assert (await provider.get_spool("1")).remaining_g == 700.0              # set twice (or thrice) = set once
    assert (await provider.get_spool("2")).remaining_g == 500.0              # and no other spool moved


async def test_set_remaining_on_an_unknown_spool_is_a_neutral_error(provider):
    if WRITE_WEIGHT not in provider.capabilities:
        pytest.skip("provider cannot write weights")
    with pytest.raises(InventoryProviderError):
        await provider.set_remaining("999", 1.0)


async def test_profile_links_round_trip_or_are_refused(provider):
    if PROFILE_LINKS_WRITE not in provider.capabilities:
        with pytest.raises(NotSupported):
            await provider.set_profile_links("1", {"Printer A": ["PLA @A"]})
        return
    assert PROFILE_LINKS_READ in provider.capabilities                     # writing links implies being able to read them
    assert next(m for m in await provider.list_materials() if m.ref == "1").profile_links == {}
    updated = await provider.set_profile_links("1", {"Printer A": ["PLA @A", "PLA+ @A"]})
    assert updated.ref == "1"
    assert next(m for m in await provider.list_materials() if m.ref == "1").profile_links == {"Printer A": ["PLA @A", "PLA+ @A"]}


async def test_label_parsing_works_with_the_capability_and_is_refused_without_it(provider):
    if LABEL_SCAN not in provider.capabilities:
        with pytest.raises(NotSupported):
            provider.parse_label("anything")
        return
    assert provider.parse_label("definitely not a label @@") is None


async def test_spool_url_is_a_string_or_none(provider):
    assert provider.spool_url("1") is None or isinstance(provider.spool_url("1"), str)


# --- a provider with no optional capabilities is still a valid provider -------------------------------------------------

async def test_a_minimal_provider_gets_not_supported_from_every_optional_method():
    class Minimal(FilamentInventoryProvider):
        async def test_connection(self): return {"ok": True}
        async def list_materials(self): return []
        async def list_spools(self): return []
        async def get_spool(self, spool_ref): return None

    p = Minimal()
    assert p.capabilities == frozenset()
    with pytest.raises(NotSupported) as e1:
        await p.set_remaining("1", 1.0)
    with pytest.raises(NotSupported) as e2:
        await p.set_profile_links("1", {})
    with pytest.raises(NotSupported) as e3:
        p.parse_label("x")
    assert (e1.value.capability, e2.value.capability, e3.value.capability) == (WRITE_WEIGHT, PROFILE_LINKS_WRITE, LABEL_SCAN)
    assert p.spool_url("1") is None


def test_the_abc_cannot_be_instantiated_without_the_required_methods():
    class Incomplete(FilamentInventoryProvider):
        async def test_connection(self): return {}
    with pytest.raises(TypeError):
        Incomplete()


# --- Spoolman specifics: wire format, labels, error mapping ---------------------------------------------------------------

async def test_spoolman_set_remaining_patches_the_absolute_weight(spoolman_upstream):
    await _spoolman().set_remaining("1", 640.5)
    patch_req = next(r for r in spoolman_upstream.requests if r.method == "PATCH")
    assert patch_req.url.path == "/api/v1/spool/1" and json.loads(patch_req.content) == {"remaining_weight": 640.5}
    assert patch_req.headers["X-API-Key"] == "key"


async def test_spoolman_links_are_double_json_encoded_on_the_wire(spoolman_upstream):
    await _spoolman().set_profile_links("1", {"Printer A": ["PLA @A"]})
    stored = spoolman_mock._FILAMENTS[0]["extra"]["orca_profiles"]
    assert json.loads(json.loads(stored)) == {"Printer A": ["PLA @A"]}


async def test_spoolman_malformed_links_read_as_empty(spoolman_upstream):
    spoolman_mock._FILAMENTS[0]["extra"]["orca_profiles"] = "not json"
    assert next(m for m in await _spoolman().list_materials() if m.ref == "1").profile_links == {}
    spoolman_mock._FILAMENTS[0]["extra"]["orca_profiles"] = json.dumps(json.dumps(["not", "a", "dict"]))
    assert next(m for m in await _spoolman().list_materials() if m.ref == "1").profile_links == {}


async def test_spoolman_raw_payloads_are_kept_for_the_plugins_own_alias_routes(spoolman_upstream):
    p = _spoolman()
    assert [m.raw for m in await p.list_materials()] == spoolman_mock._FILAMENTS
    assert [s.raw for s in await p.list_spools()] == spoolman_mock._SPOOLS


@pytest.mark.parametrize("text, ref", [
    ("web+spoolman:s-12", "12"), ("WEB+SPOOLMAN:S-7", "7"), ("s-9", "9"), ("see S-42 now", "42"),
    ("http://sm.local:7912/spool/show/33", "33"), (" 15 ", "15"), ("", None), ("hello", None), ("s-", None),
])
def test_spoolman_label_formats(text, ref):
    assert _spoolman().parse_label(text) == ref


def test_spoolman_spool_url_and_required_url():
    assert _spoolman().spool_url("5") == "http://spoolman.test/spool/show/5"
    with pytest.raises(ValueError):
        SpoolmanProvider(SpoolmanSettings(url=None))


async def test_spoolman_transport_error_maps_to_a_neutral_error(spoolman_upstream):
    def boom(request):
        raise httpx.ConnectError("refused")
    spoolman_upstream.handler = boom
    with pytest.raises(InventoryProviderError) as ei:
        await _spoolman().list_spools()
    assert ei.value.code == "ConnectError" and ei.value.status is None


async def test_spoolman_http_status_maps_code_and_status(spoolman_upstream):
    spoolman_upstream.handler = lambda request: httpx.Response(502, text="bad gateway")
    with pytest.raises(InventoryProviderError) as ei:
        await _spoolman().test_connection()
    assert (ei.value.code, ei.value.status) == ("502", 502)


async def test_spoolman_get_spool_distinguishes_missing_from_failure(spoolman_upstream):
    assert await _spoolman().get_spool("999") is None
    assert await _spoolman().get_spool("abc") is None
    spoolman_upstream.handler = lambda request: httpx.Response(500, text="boom")
    with pytest.raises(InventoryProviderError) as ei:
        await _spoolman().get_spool("1")
    assert ei.value.status == 500
