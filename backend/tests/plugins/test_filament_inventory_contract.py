"""Contract suite for every `filament_inventory` provider (spec §6): a new provider has to pass it.

Parametrized over the in-memory fakes, the Spoolman plugin (backed by the real tests/spoolman_mock.py app) and the Local
inventory plugin (backed by the real test database)."""
from __future__ import annotations

import copy
import json

import httpx
import pytest
from sqlalchemy import text

from app.plugins.capabilities.filament_inventory import (
    ALL_CAPABILITIES, CAPABILITY, LABEL_SCAN, MANAGE_MATERIALS, MANAGE_SPOOLS, PROFILE_LINKS_READ, PROFILE_LINKS_WRITE, TRACKS_WEIGHT,
    WRITE_WEIGHT, FilamentInventoryProvider, InvMaterial, InvSpool, InventoryProviderError, MaterialDraft, NotSupported, SpoolDraft,
)
from app.plugins.local_inventory import MANIFEST as LOCAL_MANIFEST
from app.plugins.local_inventory.provider import LocalInventoryProvider
from app.plugins.local_inventory.settings import LocalInventorySettings
from app.plugins.spoolman import MANIFEST as SPOOLMAN_MANIFEST
from app.plugins.spoolman.provider import SpoolmanProvider
from app.plugins.spoolman.settings import SpoolmanSettings
from tests import spoolman_mock
from tests.fake_providers import FakeInventoryProvider, FakeLibraryProvider


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


def _fake_library() -> FakeLibraryProvider:
    base = _fake()
    return FakeLibraryProvider(materials=list(base.materials.values()), spools=list(base.spools.values()))


def _spoolman() -> SpoolmanProvider:
    return SpoolmanProvider(SpoolmanSettings(url="http://spoolman.test", api_key="key"))


async def _local(session_factory) -> LocalInventoryProvider:
    """The same two materials / two spools the other providers start with (refs 1 and 2)."""
    async with session_factory() as s:
        for name, color in (("PLA White", "#FFFFFF"), ("PLA Black", "#000000")):
            await s.execute(text("INSERT INTO local_inv_materials (name, material, vendor, color_hex, created_at, updated_at) "
                                 "VALUES (:n, 'PLA', 'Elegoo', :c, 'x', 'x')"), {"n": name, "c": color})
        for mat, label, g in ((1, "Elegoo PLA White", 800.0), (2, "Elegoo PLA Black", 500.0)):
            await s.execute(text("INSERT INTO local_inv_spools (material_id, label, initial_g, remaining_g, created_at, updated_at) "
                                 "VALUES (:m, :l, 1000, :g, 'x', 'x')"), {"m": mat, "l": label, "g": g})
        await s.commit()
    return LocalInventoryProvider(LocalInventorySettings())


@pytest.fixture(params=["fake", "fake_library", "spoolman", "local"])
async def provider(request, spoolman_upstream, session_factory) -> FilamentInventoryProvider:
    if request.param == "local":
        return await _local(session_factory)
    return {"fake": _fake, "fake_library": _fake_library, "spoolman": _spoolman}[request.param]()


# --- declaration ---------------------------------------------------------------------------------------------------

async def test_capabilities_are_a_known_subset_and_the_bundled_manifest_declares_the_same(provider):
    assert provider.capabilities <= ALL_CAPABILITIES
    if isinstance(provider, SpoolmanProvider):
        assert SPOOLMAN_MANIFEST.provides[CAPABILITY].features == provider.capabilities
    if isinstance(provider, LocalInventoryProvider):
        assert LOCAL_MANIFEST.provides[CAPABILITY].features == provider.capabilities


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


# --- library management: works with MANAGE_*, refused without it ---------------------------------------------------------

async def test_materials_can_be_created_updated_and_archived_or_every_call_is_refused(provider):
    if MANAGE_MATERIALS not in provider.capabilities:
        with pytest.raises(NotSupported) as e:
            await provider.create_material(MaterialDraft(name="X"))
        assert e.value.capability == MANAGE_MATERIALS
        with pytest.raises(NotSupported):
            await provider.update_material("1", {"name": "Y"})
        with pytest.raises(NotSupported):
            await provider.archive_material("1")
        return
    created = await provider.create_material(MaterialDraft(name="PETG Blue", material="PETG", color_hex="#0000FF", vendor="Acme", diameter=1.75))
    assert isinstance(created.ref, str) and (created.name, created.material, created.color_hex, created.vendor) == ("PETG Blue", "PETG", "#0000FF", "Acme")
    assert created.ref in {m.ref for m in await provider.list_materials()}
    updated = await provider.update_material(created.ref, {"name": "PETG Navy", "vendor": None})
    assert (updated.name, updated.vendor, updated.material) == ("PETG Navy", None, "PETG")          # only the sent fields changed
    archived = await provider.archive_material(created.ref)
    assert archived.archived is True and next(m for m in await provider.list_materials() if m.ref == created.ref).archived is True
    assert (await provider.archive_material(created.ref, archived=False)).archived is False       # restorable
    with pytest.raises(InventoryProviderError) as bad:
        await provider.update_material("999", {"name": "x"})
    assert bad.value.status == 404
    with pytest.raises(InventoryProviderError) as invalid:
        await provider.create_material(MaterialDraft(name="  "))
    assert invalid.value.status == 422


async def test_spools_can_be_created_updated_weighed_and_archived_or_every_call_is_refused(provider):
    if MANAGE_SPOOLS not in provider.capabilities:
        with pytest.raises(NotSupported) as e:
            await provider.create_spool(SpoolDraft(material_ref="1"))
        assert e.value.capability == MANAGE_SPOOLS
        with pytest.raises(NotSupported):
            await provider.update_spool("1", {"location": "Shelf"})
        with pytest.raises(NotSupported):
            await provider.archive_spool("1")
        return
    created = await provider.create_spool(SpoolDraft(material_ref="1", location="Shelf B", initial_g=1000.0))
    assert isinstance(created.ref, str) and created.material_ref == "1" and created.location == "Shelf B" and created.label
    assert created.remaining_g == 1000.0                                      # remaining defaults to the initial weight
    moved = await provider.update_spool(created.ref, {"location": "Drawer 2", "label": "Blue #2"})
    assert (moved.location, moved.label) == ("Drawer 2", "Blue #2")
    if WRITE_WEIGHT in provider.capabilities:
        await provider.set_remaining(created.ref, 640.0)
        assert (await provider.get_spool(created.ref)).remaining_g == 640.0     # change remaining weight
    assert (await provider.archive_spool(created.ref)).archived is True
    assert next(s for s in await provider.list_spools() if s.ref == created.ref).archived is True
    with pytest.raises(InventoryProviderError) as no_material:
        await provider.create_spool(SpoolDraft(material_ref="999"))
    assert no_material.value.status == 404
    with pytest.raises(InventoryProviderError) as bad_field:
        await provider.update_spool(created.ref, {"remaining_g": 1.0})            # weight has its own (absolute) call
    assert bad_field.value.status == 422


async def test_library_semantics_every_provider_follows(provider):
    """Documented on the ABC: None clears optional fields, name/label cannot be cleared, archiving never cascades and is
    editable, create_spool rejects an archived material and remaining > initial, remaining defaults to initial."""
    if MANAGE_MATERIALS not in provider.capabilities or MANAGE_SPOOLS not in provider.capabilities:
        pytest.skip("provider does not manage its library")
    m = await provider.create_material(MaterialDraft(name="ASA", vendor="Acme", material="ASA", diameter=1.75))
    cleared = await provider.update_material(m.ref, {"vendor": None, "diameter": None})
    assert (cleared.vendor, cleared.diameter, cleared.name, cleared.material) == (None, None, "ASA", "ASA")      # None clears; others stay
    with pytest.raises(InventoryProviderError) as no_name:
        await provider.update_material(m.ref, {"name": ""})
    assert no_name.value.status == 422

    s = await provider.create_spool(SpoolDraft(material_ref=m.ref, initial_g=800.0))
    assert (s.initial_g, s.remaining_g) == (800.0, 800.0)
    assert (await provider.update_spool(s.ref, {"location": "Drawer"})).location == "Drawer"
    assert (await provider.update_spool(s.ref, {"location": None})).location is None                            # None clears
    with pytest.raises(InventoryProviderError) as no_label:
        await provider.update_spool(s.ref, {"label": ""})
    assert no_label.value.status == 422
    with pytest.raises(InventoryProviderError) as too_much:
        await provider.create_spool(SpoolDraft(material_ref=m.ref, initial_g=100.0, remaining_g=200.0))
    assert too_much.value.status == 422

    await provider.archive_material(m.ref)                                                                       # no cascade
    assert next(x for x in await provider.list_spools() if x.ref == s.ref).archived is False
    with pytest.raises(InventoryProviderError) as archived_parent:
        await provider.create_spool(SpoolDraft(material_ref=m.ref))
    assert archived_parent.value.status == 422
    await provider.archive_spool(s.ref)
    assert (await provider.get_spool(s.ref)).archived is True                                                    # still readable
    assert (await provider.update_spool(s.ref, {"location": "Box"})).location == "Box"                           # and editable
    if WRITE_WEIGHT in provider.capabilities:
        await provider.set_remaining(s.ref, 100.0)
        assert (await provider.get_spool(s.ref)).remaining_g == 100.0                                            # and weighable


async def test_spools_report_their_initial_weight_when_the_provider_knows_it(provider):
    s1 = next(s for s in await provider.list_spools() if s.ref == "1")
    if isinstance(provider, SpoolmanProvider):
        assert s1.initial_g == 1000.0                                  # the filament's weight
    else:
        assert s1.initial_g is None or isinstance(s1.initial_g, float)


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
    for call in (p.create_material(MaterialDraft(name="x")), p.update_material("1", {}), p.archive_material("1"),
                 p.create_spool(SpoolDraft(material_ref="1")), p.update_spool("1", {}), p.archive_spool("1")):
        with pytest.raises(NotSupported):
            await call
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
