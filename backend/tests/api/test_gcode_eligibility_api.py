"""G-code machine eligibility over the API (BIZ-263): upload / file detail / filter, job creation per printer, target materialisation."""
from unittest.mock import patch

import pytest

from app import plugins
from app.models import Job
from app.plugins.manifest import Manufacturer, PrinterModel
from tests.plugins.dummy_plugin import make_manifest

GCODE = b"; filament used [g] = 12.5\n; estimated printing time (normal mode) = 1h 2m 3s\nG28\n"


@pytest.fixture(autouse=True)
def _library(tmp_path, monkeypatch):
    monkeypatch.setenv("THEMIS_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("THEMIS_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture(autouse=True)
def _no_queue_engine():
    with patch("app.api.routes.files.queue_engine"):
        yield


@pytest.fixture(autouse=True)
def _fake_plugin():
    saved = dict(plugins._REGISTRY)
    plugins.register_plugin(make_manifest("fake_printers", default_enabled=True, manufacturers=(
        Manufacturer("acme", "Acme", (PrinterModel("x1", "X1"), PrinterModel("x2", "X2"))),)))
    yield
    plugins._REGISTRY.clear()
    plugins._REGISTRY.update(saved)


async def model_ids(client) -> dict[str, str]:
    return {m["model_id"]: m["id"] for m in (await client.get("/api/v1/printer-models", params={"plugin_id": "fake_printers"})).json()}


async def printer_on(client, models, model) -> int:
    r = await client.post("/api/v1/printers", json={"name": f"on-{model}", "model_uuid": models[model], "connection_config": {}})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def upload(client, name="part.gcode", uuids=None):
    data = {"eligible_model_uuids": uuids} if uuids else None
    body = GCODE + f"; {name}\n".encode()           # distinct bytes per name: identical content in a folder is de-duplicated
    r = await client.post("/api/v1/files/upload", files={"file": (name, body, "application/octet-stream")}, data=data)
    assert r.status_code == 201, r.text
    return r.json()


async def post_job(client, file_id, printer_ids, **extra):
    body = {"uploaded_file_id": file_id, "printer_configs": [
        {"printer_id": p, "filament_type": "any", "filament_color": "any"} for p in printer_ids], **extra}
    with patch("app.api.routes.jobs.queue_engine"):
        return await client.post("/api/v1/jobs", json=body)


# --- upload / detail / filter ---------------------------------------------------------------------------------------

async def test_an_upload_without_eligibility_is_unknown_and_with_it_records_exactly_those_models(client):
    models = await model_ids(client)

    legacy = await upload(client, "legacy.gcode")
    known = await upload(client, "known.gcode", [models["x1"], models["x2"]])

    assert legacy["eligibility"] == {"known": False, "model_uuids": []}
    assert known["eligibility"]["known"] is True and set(known["eligibility"]["model_uuids"]) == {models["x1"], models["x2"]}
    detail = (await client.get(f"/api/v1/files/{known['id']}/eligibility")).json()
    assert detail["known"] is True and {m["display_name"] for m in detail["models"]} == {"X1", "X2"}
    assert (await client.get(f"/api/v1/files/{legacy['id']}/eligibility")).json() == {"known": False, "models": []}


async def test_an_upload_with_an_unknown_model_is_refused_and_leaves_no_file_behind(client):
    r = await client.post("/api/v1/files/upload", files={"file": ("bad.gcode", GCODE + b"; bad\n", "application/octet-stream")},
                          data={"eligible_model_uuids": ["nope"]})

    assert r.status_code == 422
    assert [f["original_filename"] for f in (await client.get("/api/v1/files")).json()] == []


async def test_put_replaces_the_set_and_a_model_file_has_no_eligibility(client, upload_3mf):
    models = await model_ids(client)
    f = await upload(client, "a.gcode", [models["x1"]])

    r = await client.put(f"/api/v1/files/{f['id']}/eligibility", json={"model_uuids": [models["x2"]]})

    assert r.status_code == 200 and [m["model_uuid"] for m in r.json()["models"]] == [models["x2"]]
    model_file = await upload_3mf("m.3mf")
    assert (await client.put(f"/api/v1/files/{model_file}/eligibility", json={"model_uuids": [models["x1"]]})).status_code == 422
    assert [f2 for f2 in (await client.get("/api/v1/files")).json() if f2["id"] == model_file][0]["eligibility"] is None
    assert (await client.get("/api/v1/files/9999/eligibility")).status_code == 404
    assert (await client.put(f"/api/v1/files/{f['id']}/eligibility", json={"model_uuids": ["nope"]})).status_code == 422


async def test_files_can_be_filtered_by_eligible_model_with_unknown_files_opt_in(client):
    models = await model_ids(client)
    x1 = await upload(client, "x1.gcode", [models["x1"]])
    x2 = await upload(client, "x2.gcode", [models["x2"]])
    legacy = await upload(client, "legacy.gcode")

    async def names(**params):
        return {f["original_filename"] for f in (await client.get("/api/v1/files", params=params)).json()}

    assert await names(eligible_model=models["x1"]) == {"x1.gcode"}
    assert await names(eligible_model=models["x1"], include_unknown_eligibility="true") == {"x1.gcode", "legacy.gcode"}
    assert x2["id"] and legacy["id"]


# --- job creation ---------------------------------------------------------------------------------------------------

async def test_a_job_for_an_eligible_printer_is_created(client):
    models = await model_ids(client)
    pid = await printer_on(client, models, "x1")
    f = await upload(client, "ok.gcode", [models["x1"]])

    r = await post_job(client, f["id"], [pid])

    assert r.status_code == 201, r.text


async def test_each_picked_printer_is_judged_on_its_own_and_only_the_incompatible_one_is_named(client):
    models = await model_ids(client)
    good, bad = await printer_on(client, models, "x1"), await printer_on(client, models, "x2")
    f = await upload(client, "ok.gcode", [models["x1"]])

    r = await post_job(client, f["id"], [good, bad])

    assert r.status_code == 422
    assert "on-x2" in r.json()["detail"] and "on-x1" not in r.json()["detail"] and "Acme X1" in r.json()["detail"]
    assert (await post_job(client, f["id"], [good])).status_code == 201


async def test_unknown_eligibility_is_refused_until_the_user_confirms_and_the_confirmation_is_stored(client, session_factory):
    models = await model_ids(client)
    pid = await printer_on(client, models, "x1")
    f = await upload(client, "legacy.gcode")

    refused = await post_job(client, f["id"], [pid])
    confirmed = await post_job(client, f["id"], [pid], confirm_unknown_eligibility=True)

    assert refused.status_code == 409 and "no recorded machine eligibility" in refused.json()["detail"]
    assert confirmed.status_code == 201
    async with session_factory() as s:
        assert (await s.get(Job, confirmed.json()["id"])).eligibility_confirmed is True


async def test_confirming_has_no_effect_on_an_incompatible_known_file(client):
    models = await model_ids(client)
    pid = await printer_on(client, models, "x2")
    f = await upload(client, "ok.gcode", [models["x1"]])

    assert (await post_job(client, f["id"], [pid], confirm_unknown_eligibility=True)).status_code == 422


async def test_editing_a_job_onto_an_incompatible_printer_is_refused_and_keeps_the_old_config(client):
    models = await model_ids(client)
    good, bad = await printer_on(client, models, "x1"), await printer_on(client, models, "x2")
    f = await upload(client, "ok.gcode", [models["x1"]])
    job = (await post_job(client, f["id"], [good])).json()

    r = await client.patch(f"/api/v1/jobs/{job['id']}/configs", json={"printer_configs": [
        {"printer_id": bad, "filament_type": "any", "filament_color": "any"}]})

    assert r.status_code == 422
    assert [c["printer_id"] for c in (await client.get(f"/api/v1/jobs/{job['id']}/details")).json()["printer_configs"]] == [good]


async def test_a_make_model_target_only_materialises_onto_eligible_printers(client, session_factory):
    models = await model_ids(client)
    x1 = await printer_on(client, models, "x1")
    x2 = await printer_on(client, models, "x2")
    for pid in (x1, x2):
        await client.patch(f"/api/v1/printers/{pid}", json={"current_orca_printer_profile": "Shared 0.4"})
    f = await upload(client, "ok.gcode", [models["x1"]])

    with patch("app.api.routes.jobs.queue_engine"):
        r = await client.post("/api/v1/jobs", json={"uploaded_file_id": f["id"], "model_targets": [{"machine_profile": "Shared 0.4"}]})

    assert r.status_code == 201, r.text
    assert [c["printer_id"] for c in (await client.get(f"/api/v1/jobs/{r.json()['id']}/details")).json()["printer_configs"]] == [x1]


async def test_a_make_model_target_nobody_eligible_can_take_is_refused(client):
    models = await model_ids(client)
    pid = await printer_on(client, models, "x2")
    await client.patch(f"/api/v1/printers/{pid}", json={"current_orca_printer_profile": "Shared 0.4"})
    f = await upload(client, "ok.gcode", [models["x1"]])

    with patch("app.api.routes.jobs.queue_engine"):
        r = await client.post("/api/v1/jobs", json={"uploaded_file_id": f["id"], "model_targets": [{"machine_profile": "Shared 0.4"}]})

    assert r.status_code == 422 and "No Shared 0.4 printer can take this file" in r.json()["detail"]
