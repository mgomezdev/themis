"""Model -> slicer machine profile mapping (BIZ-294): CRUD, variant validation, default uniqueness, a printer's nozzle variant, the
resolved profile on printers, and suggestions from the provider (stubbed to the Laminus profiles contract)."""
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from app.models import PrinterModelSlicerProfile
from tests.fake_providers import FakeSlicingProvider
from tests.services.test_slicing_provider_contract import sidecar  # noqa: F401  (fixture)

pytestmark = pytest.mark.asyncio

P1S_04 = "Bambu Lab P1S 0.4 nozzle"
P1S_06 = "Bambu Lab P1S 0.6 nozzle"
P1S_04_HF = "Bambu Lab P1S 0.4 high flow nozzle"


@pytest.fixture
async def p1s(client, create_printer, model_uuid_of):
    """(printer_id, model_uuid) of a Bambu P1S with no override: its profile comes only from the model mapping."""
    pid = await create_printer(machine_profile_override=None)
    return pid, await model_uuid_of(pid)


def url(model: str, *tail) -> str:
    return "/".join(["/api/v1/printer-models", model, "slicer-profiles", *map(str, tail)])


async def add(client, model, profile=P1S_04, d=0.4, flow="standard", **kw):
    r = await client.post(url(model), json={"provider_plugin_id": "laminus", "nozzle_diameter_mm": d, "flow": flow,
                                            "machine_profile": profile, **kw})
    return r


async def test_the_first_mapping_of_a_model_becomes_its_default(client, p1s):
    _, model = p1s

    r = await add(client, model)

    assert r.status_code == 201, r.text
    assert r.json()["is_default"] is True and r.json()["machine_profile"] == P1S_04
    assert (await client.get(url(model))).json() == [r.json()]


async def test_a_second_variant_is_not_the_default_unless_asked(client, p1s):
    _, model = p1s
    first = (await add(client, model)).json()

    second = (await add(client, model, P1S_06, d=0.6)).json()

    assert second["is_default"] is False
    rows = {r["id"]: r for r in (await client.get(url(model))).json()}
    assert rows[first["id"]]["is_default"] is True


async def test_making_another_variant_the_default_moves_the_default_so_exactly_one_remains(client, p1s, session_factory):
    _, model = p1s
    first = (await add(client, model)).json()

    second = (await add(client, model, P1S_06, d=0.6, is_default=True)).json()

    assert second["is_default"] is True
    async with session_factory() as s:
        rows = (await s.execute(select(PrinterModelSlicerProfile))).scalars().all()
    assert [r.id for r in rows if r.is_default] == [second["id"]] and first["id"] in {r.id for r in rows}
    third = (await client.patch(url(model, first["id"]), json={"is_default": True})).json()
    assert third["is_default"] is True
    assert [r["id"] for r in (await client.get(url(model))).json() if r["is_default"]] == [first["id"]]


async def test_the_database_itself_refuses_two_defaults_for_one_model_and_provider(p1s, session_factory, client):
    from sqlalchemy.exc import IntegrityError
    _, model = p1s
    await add(client, model)
    with pytest.raises(IntegrityError):
        async with session_factory() as s:
            s.add(PrinterModelSlicerProfile(model_uuid=model, provider_plugin_id="laminus", nozzle_diameter_mm=0.6, flow="standard",
                                            machine_profile="x", is_default=True))
            await s.commit()


async def test_a_default_cannot_be_unset_directly(client, p1s):
    _, model = p1s
    first = (await add(client, model)).json()
    r = await client.patch(url(model, first["id"]), json={"is_default": False})
    assert r.status_code == 422
    assert (await client.get(url(model))).json()[0]["is_default"] is True


async def test_the_same_variant_cannot_be_mapped_twice_but_flow_distinguishes_variants(client, p1s):
    _, model = p1s
    await add(client, model)

    dup = await add(client, model, "other", d=0.4)
    hf = await add(client, model, P1S_04_HF, d=0.4, flow="high_flow")

    assert dup.status_code == 409
    assert hf.status_code == 201 and hf.json()["is_default"] is False


@pytest.mark.parametrize("body", [
    {"nozzle_diameter_mm": 0},
    {"nozzle_diameter_mm": -0.4},
    {"nozzle_diameter_mm": 9},
    {"flow": "turbo"},
    {"machine_profile": "   "},
    {"provider_plugin_id": "not_a_slicer"},
])
async def test_invalid_mappings_are_refused(client, p1s, body):
    _, model = p1s
    good = {"provider_plugin_id": "laminus", "nozzle_diameter_mm": 0.4, "flow": "standard", "machine_profile": P1S_04}
    r = await client.post(url(model), json={**good, **body})
    assert r.status_code == 422
    assert (await client.get(url(model))).json() == []


async def test_unknown_model_and_unknown_mapping_are_404(client, p1s):
    _, model = p1s
    assert (await client.get(url("nope"))).status_code == 404
    assert (await add(client, "nope")).status_code == 404
    assert (await client.patch(url(model, 999), json={"machine_profile": "x"})).status_code == 404
    assert (await client.delete(url(model, 999))).status_code == 404


async def test_editing_a_mapping_changes_its_profile(client, p1s):
    _, model = p1s
    row = (await add(client, model)).json()
    r = await client.patch(url(model, row["id"]), json={"machine_profile": "Renamed"})
    assert r.status_code == 200 and r.json()["machine_profile"] == "Renamed"
    assert (await client.get(url(model))).json()[0]["machine_profile"] == "Renamed"


async def test_the_default_cannot_be_deleted_while_other_variants_exist(client, p1s):
    pid, model = p1s
    first = (await add(client, model)).json()
    second = (await add(client, model, P1S_06, d=0.6)).json()

    assert (await client.delete(url(model, first["id"]))).status_code == 409
    assert (await client.delete(url(model, second["id"]))).status_code == 204
    assert (await client.delete(url(model, first["id"]))).status_code == 409       # the last one: the p1s printer still follows it
    await client.delete(f"/api/v1/printers/{pid}")
    assert (await client.delete(url(model, first["id"]))).status_code == 204       # nobody follows it any more
    assert (await client.get(url(model))).json() == []


async def test_a_variant_a_printer_uses_cannot_be_deleted(client, p1s):
    pid, model = p1s
    await add(client, model)
    six = (await add(client, model, P1S_06, d=0.6)).json()
    assert (await client.patch(f"/api/v1/printers/{pid}", json={"nozzle_variant": {"diameter_mm": 0.6, "flow": "standard"}})
            ).status_code == 200

    r = await client.delete(url(model, six["id"]))

    assert r.status_code == 409 and "0.6mm" in r.json()["detail"]


# --- the resolved profile on printers -------------------------------------------------------------------------------------------------

def laminus_default():
    return patch("app.services.slicer_profiles.effective_provider_id", lambda preferred: preferred or "laminus")


async def test_a_printer_resolves_its_profile_from_the_model_default_and_a_variant_picks_another(client, p1s):
    pid, model = p1s
    await add(client, model)
    await add(client, model, P1S_06, d=0.6)
    with laminus_default():
        assert (await client.get(f"/api/v1/printers/{pid}")).json()["machine_profile"] == P1S_04
        assert (await client.get(f"/api/v1/printers/{pid}")).json()["model_label"] == "Bambu Lab P1S"
        assert [p["model_label"] for p in (await client.get("/api/v1/printers")).json()] == ["Bambu Lab P1S"]
        r = await client.patch(f"/api/v1/printers/{pid}", json={"nozzle_variant": {"diameter_mm": 0.6, "flow": "standard"}})
        assert r.status_code == 200 and r.json()["machine_profile"] == P1S_06
        assert [p["machine_profile"] for p in (await client.get("/api/v1/printers")).json()] == [P1S_06]
        cleared = await client.patch(f"/api/v1/printers/{pid}", json={"nozzle_variant": None})
        assert cleared.json()["nozzle_variant"] is None and cleared.json()["machine_profile"] == P1S_04


async def test_ten_identical_printers_need_no_per_printer_profile(client, create_printer, model_uuid_of):
    ids = [await create_printer(name=f"P{i}", machine_profile_override=None) for i in range(10)]
    model = await model_uuid_of(ids[0])
    await add(client, model)
    with laminus_default():
        got = {p["machine_profile"] for p in (await client.get("/api/v1/printers")).json()}
    assert got == {P1S_04}


async def test_a_raw_override_wins_over_the_mapping(client, p1s):
    pid, model = p1s
    await add(client, model)
    with laminus_default():
        r = await client.patch(f"/api/v1/printers/{pid}", json={"machine_profile_override": "My custom preset"})
    assert r.json()["machine_profile"] == "My custom preset"


async def test_an_unmapped_variant_is_refused_and_a_printer_on_none_reports_why(client, p1s, session_factory):
    pid, model = p1s
    await add(client, model)

    r = await client.patch(f"/api/v1/printers/{pid}", json={"nozzle_variant": {"diameter_mm": 0.8, "flow": "standard"}})
    assert r.status_code == 422 and "0.8mm" in r.json()["detail"]
    assert (await client.get(f"/api/v1/printers/{pid}")).json()["nozzle_variant"] is None
    for bad in ({"diameter_mm": 0.4, "flow": "turbo"}, {"diameter_mm": "x"}, {"flow": "standard"}):
        assert (await client.patch(f"/api/v1/printers/{pid}", json={"nozzle_variant": bad})).status_code == 422

    with laminus_default():
        none_yet = (await client.get(f"/api/v1/printers/{pid}")).json()
        async with session_factory() as s:                       # (the API refuses to remove the last profile a printer follows)
            for r in (await s.execute(select(PrinterModelSlicerProfile))).scalars():
                await s.delete(r)
            await s.commit()
        gone = (await client.get(f"/api/v1/printers/{pid}")).json()
    assert none_yet["machine_profile"] == P1S_04
    assert gone["machine_profile"] is None and gone["machine_profile_reason"].startswith("no machine profile for ")


async def test_creating_a_printer_with_an_unmapped_variant_is_refused(client, p1s):
    pid, model = p1s
    await add(client, model)
    r = await client.post("/api/v1/printers", json={"name": "N", "printer_type": "bambu", "connection_config": {},
                                                    "nozzle_variant": {"diameter_mm": 0.6, "flow": "standard"}})
    assert r.status_code == 422
    ok = await add(client, model, P1S_06, d=0.6)
    assert ok.status_code == 201
    r = await client.post("/api/v1/printers", json={"name": "N", "printer_type": "bambu", "connection_config": {},
                                                    "nozzle_variant": {"diameter_mm": 0.6, "flow": "standard"}})
    assert r.status_code == 201 and r.json()["nozzle_variant"] == {"diameter_mm": 0.6, "flow": "standard"}


# --- suggestions from the provider (Laminus profiles contract, stubbed) ------------------------------------------------------------------

SUGGEST = {"catalog_version": "abc123def4567890", "candidates": [
    {"ref": "machine:bambu-lab-p1s-0.4-nozzle", "uuid": "u-1", "name": P1S_04, "nozzle_diameter_mm": 0.4, "flow": "standard",
     "confidence": 0.98, "match_reason": "exact_model"},
    {"ref": "machine:bambu-lab-p1s-0.6-nozzle", "uuid": "u-2", "name": P1S_06, "nozzle_diameter_mm": 0.6, "flow": "standard",
     "confidence": 0.9, "match_reason": "exact_model"}]}
VARIANTS = {"catalog_version": "abc123def4567890", "variants": [
    {"ref": "machine:bambu-lab-p1s-0.4-nozzle", "uuid": "u-1", "name": P1S_04, "nozzle_diameter_mm": 0.4, "flow": "standard",
     "is_default_guess": True},
    {"ref": "machine:bambu-lab-p1s-0.4-high-flow-nozzle", "uuid": "u-3", "name": P1S_04_HF, "nozzle_diameter_mm": 0.4,
     "flow": "high_flow", "is_default_guess": False}]}


def stub_laminus(sidecar, suggest=SUGGEST, variants=VARIANTS):  # noqa: F811
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.content))
        if request.url.path == "/api/profiles/machines/suggest":
            return httpx.Response(200, json=suggest, headers={"X-Catalog-Version": suggest["catalog_version"]})
        if request.url.path == "/api/profiles/models/variants":
            return httpx.Response(200, json=variants)
        return httpx.Response(404)
    sidecar.handler = handler
    return seen


def real_laminus_provider():
    from app.services.providers.laminus.adapter import LaminusSlicingProvider
    return patch("app.api.routes.printer_model_slicer_profiles.slicing_provider_for",
                 lambda pid: LaminusSlicingProvider("http://laminus.test"))


async def test_suggestions_come_from_the_provider_with_the_model_identity(client, p1s, sidecar):  # noqa: F811
    _, model = p1s
    seen = stub_laminus(sidecar)

    with real_laminus_provider():
        body = (await client.get(url(model, "suggestions"), params={"provider_plugin_id": "laminus"})).json()

    assert body["available"] is True and body["error"] is None
    assert [c["name"] for c in body["candidates"]] == [P1S_04, P1S_06]
    assert body["candidates"][0] == {"name": P1S_04, "nozzle_diameter_mm": 0.4, "flow": "standard",
                                     "ref": "machine:bambu-lab-p1s-0.4-nozzle", "confidence": 0.98, "match_reason": "exact_model"}
    assert [v["is_default_guess"] for v in body["variants"]] == [True, False]
    assert body["variants"][1]["flow"] == "high_flow"
    import json
    sent = json.loads(next(c for p, c in seen if p == "/api/profiles/machines/suggest"))
    assert sent["model"] == "P1S" and sent["manufacturer"] == "Bambu Lab" and sent["model_key"] == "bambu/p1s"


async def test_suggestions_degrade_to_manual_entry_when_the_provider_cannot_help(client, p1s, sidecar):  # noqa: F811
    _, model = p1s
    sidecar.handler = lambda request: httpx.Response(404)            # an older sidecar without the profiles API
    with real_laminus_provider():
        body = (await client.get(url(model, "suggestions"), params={"provider_plugin_id": "laminus"})).json()
    assert body["available"] is False and body["candidates"] == [] and body["variants"] == [] and "404" in body["error"]

    with patch("app.api.routes.printer_model_slicer_profiles.slicing_provider_for", lambda pid: FakeSlicingProvider()):
        body = (await client.get(url(model, "suggestions"), params={"provider_plugin_id": "laminus"})).json()
    assert body["available"] is False and "cannot suggest" in body["error"]

    with patch("app.api.routes.printer_model_slicer_profiles.slicing_provider_for", lambda pid: None):
        body = (await client.get(url(model, "suggestions"), params={"provider_plugin_id": "laminus"})).json()
    assert body["available"] is False and "No slicing provider" in body["error"]
    assert (await add(client, model)).status_code == 201             # manual entry never needed the provider


async def test_an_empty_suggestion_list_is_available_not_an_error(client, p1s, sidecar):  # noqa: F811
    _, model = p1s
    stub_laminus(sidecar, {"catalog_version": "x", "candidates": []}, {"catalog_version": "x", "variants": []})
    with real_laminus_provider():
        body = (await client.get(url(model, "suggestions"), params={"provider_plugin_id": "laminus"})).json()
    assert body["available"] is True and body["candidates"] == [] and body["variants"] == []


async def test_the_model_registry_listing_says_how_many_profiles_each_model_has_mapped(client, p1s):
    _, model = p1s
    def count(rows):
        return {m["id"]: m["slicer_profile_count"] for m in rows}[model]
    assert count((await client.get("/api/v1/printer-models")).json()) == 0
    await add(client, model)
    await add(client, model, P1S_06, d=0.6)
    assert count((await client.get("/api/v1/printer-models")).json()) == 2


# --- review fixes ----------------------------------------------------------------------------------------------------------------------

async def test_changing_a_printers_model_clears_a_variant_the_new_model_does_not_map_and_a_stale_override(client, p1s):
    pid, model = p1s
    await add(client, model)
    await add(client, model, P1S_06, d=0.6)
    other = next(m for m in (await client.get("/api/v1/printer-models")).json() if m["model_id"] == "x1c")["id"]
    await add(client, other, "X1C 0.4")                                  # the new model maps 0.4 only
    await client.patch(f"/api/v1/printers/{pid}", json={"nozzle_variant": {"diameter_mm": 0.6, "flow": "standard"},
                                                        "machine_profile_override": "Old custom"})

    r = await client.patch(f"/api/v1/printers/{pid}", json={"model_id": "x1c"})

    assert r.status_code == 200 and r.json()["model_uuid"] == other
    assert r.json()["nozzle_variant"] is None and r.json()["machine_profile_override"] is None


async def test_changing_a_printers_model_keeps_a_variant_the_new_model_also_maps_and_an_override_in_the_same_body(client, p1s):
    pid, model = p1s
    await add(client, model)
    await add(client, model, P1S_06, d=0.6)
    other = next(m for m in (await client.get("/api/v1/printer-models")).json() if m["model_id"] == "x1c")["id"]
    await add(client, other, "X1C 0.4")
    await add(client, other, "X1C 0.6", d=0.6)
    await client.patch(f"/api/v1/printers/{pid}", json={"nozzle_variant": {"diameter_mm": 0.6, "flow": "standard"}})

    r = await client.patch(f"/api/v1/printers/{pid}", json={"model_id": "x1c", "machine_profile_override": "Fresh custom"})

    assert r.json()["nozzle_variant"] == {"diameter_mm": 0.6, "flow": "standard"}
    assert r.json()["machine_profile_override"] == "Fresh custom"


async def test_the_last_default_cannot_be_deleted_while_a_printer_of_the_model_follows_the_default(client, p1s):
    pid, model = p1s
    only = (await add(client, model)).json()

    r = await client.delete(url(model, only["id"]))

    assert r.status_code == 409 and "follow" in r.json()["detail"]
    assert (await client.get(url(model))).json()[0]["id"] == only["id"]
    await client.delete(f"/api/v1/printers/{pid}")
    assert (await client.delete(url(model, only["id"]))).status_code == 204


async def test_the_last_default_can_go_when_every_printer_has_its_own_override_or_other_model(client, p1s, create_printer):
    pid, model = p1s
    only = (await add(client, model)).json()
    await client.patch(f"/api/v1/printers/{pid}", json={"machine_profile_override": "Mine"})
    assert (await client.delete(url(model, only["id"]))).status_code == 204


async def test_a_backup_row_missing_its_provider_is_skipped_with_a_warning_and_a_model_never_ends_up_without_a_default(client, p1s):
    import io, json
    pid, model = p1s
    row = lambda d, name, default: {"plugin_id": "bambu", "manufacturer_id": "bambu", "model_id": "p1s", "provider_plugin_id": "laminus",
                                    "nozzle_diameter_mm": d, "flow": "standard", "machine_profile": name, "is_default": default}
    bad = {k: v for k, v in row(0.8, "x", False).items() if k != "provider_plugin_id"}
    backup = {"themis_backup_version": 1, "printers": [], "slicer_profiles": [row(0.4, "A", False), row(0.6, "B", False), bad]}

    resp = await client.post("/api/v1/settings/fleet-import", files={"file": ("b.json", io.BytesIO(json.dumps(backup).encode()), "application/json")})

    assert resp.status_code == 200, resp.text
    assert any("skipped" in w for w in resp.json()["warnings"])
    rows = (await client.get(url(model))).json()
    assert sorted((r["nozzle_diameter_mm"], r["is_default"]) for r in rows) == [(0.4, True), (0.6, False)]
