"""Core printer-model registry (BIZ-262): stable UUIDs keyed to plugin model keys, upsert without duplicates, dormancy."""
import pytest
from sqlalchemy import select

from app import plugins
from app.models import PrinterModelRecord
from app.plugins.manifest import Manufacturer, PrinterModel
from app.services import printer_model_registry as registry
from tests.plugins.dummy_plugin import make_manifest


def fake(*, name_suffix="", extra=False):
    models = [PrinterModel("x1", "X1" + name_suffix), PrinterModel("x1_pro", "X1 Pro", bed_mm=(300, 300), toolheads=2)]
    mfrs = [Manufacturer("acme", "Acme", tuple(models)), Manufacturer("globex", "Globex", (PrinterModel("g1", "G1"),))]
    if extra:
        mfrs.append(Manufacturer("initech", "Initech", (PrinterModel("i1", "I1"),)))
    return make_manifest("fake_printers", manufacturers=tuple(mfrs), default_enabled=True)


@pytest.fixture(autouse=True)
def _fake_plugin():
    saved = dict(plugins._REGISTRY)
    plugins.register_plugin(fake())
    yield
    plugins._REGISTRY.clear()
    plugins._REGISTRY.update(saved)


async def rows(session_factory):
    async with session_factory() as s:
        return {(r.plugin_id, r.manufacturer_id, r.model_id): r for r in (await s.execute(select(PrinterModelRecord))).scalars()}


async def sync(session_factory):
    async with session_factory() as s:
        created = await registry.sync_registry(s)
        await s.commit()
    return created


async def test_a_plugin_contributing_several_manufacturers_gets_one_uuid_per_model(session_factory):
    await sync(session_factory)

    got = await rows(session_factory)

    keys = {k for k in got if k[0] == "fake_printers"}
    assert keys == {("fake_printers", "acme", "x1"), ("fake_printers", "acme", "x1_pro"), ("fake_printers", "globex", "g1")}
    assert len({got[k].id for k in keys}) == 3
    pro = got[("fake_printers", "acme", "x1_pro")]
    assert (pro.display_name, pro.manufacturer_name, pro.toolheads, pro.bed_x_mm) == ("X1 Pro", "Acme", 2, 300.0)


async def test_sync_is_idempotent_across_restarts_and_creates_no_duplicates(session_factory):
    await sync(session_factory)
    first = {k: r.id for k, r in (await rows(session_factory)).items()}

    created_again = await sync(session_factory)

    assert created_again == 0
    assert {k: r.id for k, r in (await rows(session_factory)).items()} == first


async def test_plugin_upgrade_keeps_uuids_updates_names_and_keeps_the_users_enabled_choice(session_factory):
    await sync(session_factory)
    before = await rows(session_factory)
    async with session_factory() as s:
        await registry.set_enabled(s, before[("fake_printers", "acme", "x1")].id, False)
        await s.commit()

    plugins._REGISTRY.pop("fake_printers")
    plugins.register_plugin(fake(name_suffix=" Mk2", extra=True))      # the upgraded plugin renames a model and adds one
    await sync(session_factory)
    after = await rows(session_factory)

    x1 = after[("fake_printers", "acme", "x1")]
    assert x1.id == before[("fake_printers", "acme", "x1")].id
    assert (x1.display_name, x1.enabled) == ("X1 Mk2", False)
    assert ("fake_printers", "initech", "i1") in after
    assert after[("fake_printers", "initech", "i1")].enabled is True


async def test_a_removed_plugin_marks_its_models_dormant_but_never_deletes_or_rekeys_them(session_factory):
    await sync(session_factory)
    before = {k: r.id for k, r in (await rows(session_factory)).items() if k[0] == "fake_printers"}

    plugins._REGISTRY.pop("fake_printers")
    await sync(session_factory)

    after = await rows(session_factory)
    assert {k: r.id for k, r in after.items() if k[0] == "fake_printers"} == before
    assert all(not after[k].declared for k in before)
    async with session_factory() as s:
        listed = {m["id"]: m for m in await registry.list_models(s, plugin_id="fake_printers")}
        usable = await registry.list_models(s, plugin_id="fake_printers", usable_only=True)
    assert {m["dormant_reason"] for m in listed.values()} == {"plugin_removed"}
    assert usable == []


async def test_a_model_the_plugin_stops_declaring_is_dormant_while_its_siblings_stay_usable(session_factory):
    await sync(session_factory)
    plugins._REGISTRY.pop("fake_printers")
    plugins.register_plugin(make_manifest("fake_printers", manufacturers=(Manufacturer("acme", "Acme", (PrinterModel("x1", "X1"),)),)))
    await sync(session_factory)

    async with session_factory() as s:
        by_model = {m["model_id"]: m for m in await registry.list_models(s, plugin_id="fake_printers")}

    assert by_model["x1_pro"]["dormant_reason"] == "model_removed"
    assert by_model["g1"]["dormant_reason"] == "model_removed"
    assert by_model["x1"]["dormant_reason"] in (None, "plugin_disabled")


async def test_free_text_discovery_matches_one_known_model_or_nothing(session_factory):
    await sync(session_factory)
    async with session_factory() as s:
        hit = await registry.match_free_text(s, "Acme X1 Pro", plugin_id="fake_printers")
        by_model = {m["model_id"]: m["id"] for m in await registry.list_models(s, plugin_id="fake_printers")}
        miss = await registry.match_free_text(s, "Totally Unknown 9000")
        blank = await registry.match_free_text(s, "")

    assert hit == by_model["x1_pro"]
    assert miss is None and blank is None


async def test_a_disabled_plugin_makes_its_models_dormant_without_touching_their_uuids(session_factory):
    plugins._REGISTRY.pop("fake_printers")
    plugins.register_plugin(make_manifest("fake_printers", default_enabled=False, manufacturers=(
        Manufacturer("acme", "Acme", (PrinterModel("x1", "X1"),)),)))
    await sync(session_factory)
    before = await rows(session_factory)

    async with session_factory() as s:
        listed = await registry.list_models(s, plugin_id="fake_printers")

    assert [m["dormant_reason"] for m in listed] == ["plugin_disabled"]
    assert listed[0]["id"] == before[("fake_printers", "acme", "x1")].id


async def test_two_concurrent_first_syncs_create_each_model_once_and_neither_fails(session_factory):
    import asyncio

    async def one():
        async with session_factory() as s:
            return await registry.sync_registry(s)

    created = await asyncio.gather(one(), one(), one())

    got = await rows(session_factory)
    assert sum(created) == len([k for k in got if k[0] == "fake_printers"]) + sum(1 for k in got if k[0] != "fake_printers")
    assert len([k for k in got if k[0] == "fake_printers"]) == 3


async def test_free_text_matching_two_known_models_is_ambiguous_so_nothing_is_guessed(session_factory):
    plugins.register_plugin(make_manifest("fake_printers_b", default_enabled=True, manufacturers=(
        Manufacturer("globex", "Globex", (PrinterModel("g1", "G1"),)),)))
    await sync(session_factory)
    async with session_factory() as s:
        assert await registry.match_free_text(s, "Globex G1") is None                      # two plugins declare it
        assert await registry.match_free_text(s, "Globex G1", plugin_id="fake_printers") is not None


async def test_a_disabled_model_is_never_auto_matched(session_factory):
    await sync(session_factory)
    async with session_factory() as s:
        x1 = (await registry.match_free_text(s, "Acme X1", plugin_id="fake_printers"))
        await registry.set_enabled(s, x1, False)
        await s.commit()
        assert await registry.match_free_text(s, "Acme X1", plugin_id="fake_printers") is None
