"""The job-field catalog and the read-only detail-fields route (spec §3), plus the printer minimum-field check."""
import logging

from app import plugins as plugins_registry
from app.plugins import JobFieldDef
from app.plugins.host import plugin_host
from app.plugins.job_fields import PRINTER_MIN_JOB_FIELDS, missing_min_fields
from app.plugins.manifest import HOST_API, Manufacturer, PluginManifest, PrinterModel
from app.services.job_details import UNSUPPORTED, set_details
from tests.eventing.fakes import register
from tests.plugins.dummy_plugin import DummyProvider, DummySettings


def printer_manifest(pid: str, *, fields=(), unsupported=()) -> PluginManifest:
    return PluginManifest(id=pid, name=pid, version="1.0.0", host_api=HOST_API, settings_model=DummySettings, factory=DummyProvider,
                          manufacturers=(Manufacturer(pid, pid, (PrinterModel(pid, pid),)),),
                          job_fields=tuple(JobFieldDef(f) for f in fields), unsupported_job_fields=tuple(unsupported))


async def test_the_catalog_route_lists_enabled_plugins_fields_and_drops_them_when_disabled(client):
    register(printer_manager_stub := printer_manifest("cat_prn", fields=("cat_field",), unsupported=("cat_unsupported",)))
    assert "cat_field" not in {f["name"] for f in (await client.get("/api/v1/job-fields")).json()}
    await plugin_host.update_config("cat_prn", enabled=True)
    rows = {f["name"]: f for f in (await client.get("/api/v1/job-fields")).json()}
    assert rows["cat_field"]["sources"] == [{"plugin_id": "cat_prn", "description": "", "type_hint": "", "supported": True}]
    assert rows["cat_unsupported"]["sources"][0]["supported"] is False
    await plugin_host.update_config("cat_prn", enabled=False)
    assert "cat_field" not in {f["name"] for f in (await client.get("/api/v1/job-fields")).json()}


async def test_the_detail_fields_route_answers_each_requested_field(client, create_job, session_factory):
    job_id = await create_job()
    async with session_factory() as s:
        await set_details(s, job_id, "some_plugin", {"print_end_time": "2026-10-20T13:00:00Z", "filament_used_grams": UNSUPPORTED})
        await s.commit()

    body = (await client.get(f"/api/v1/jobs/{job_id}/detail-fields", params={"fields": "print_end_time, filament_used_grams,nope,print_end_time"})).json()

    assert body["job_id"] == job_id and list(body["fields"]) == ["print_end_time", "filament_used_grams", "nope"]    # trimmed, de-duplicated
    assert body["fields"]["print_end_time"]["value"] == "2026-10-20T13:00:00Z" and body["fields"]["print_end_time"]["origin"] == "some_plugin"
    assert body["fields"]["filament_used_grams"]["status"] == "unsupported"
    assert body["fields"]["nope"]["status"] == "unknown_field"
    only = (await client.get(f"/api/v1/jobs/{job_id}/detail-fields", params={"fields": "print_end_time", "origin": "other"})).json()
    assert only["fields"]["print_end_time"]["status"] == "unknown_field"


async def test_the_detail_fields_route_rejects_a_missing_job_and_a_bad_field_list(client, create_job):
    assert (await client.get("/api/v1/jobs/999999/detail-fields", params={"fields": "a"})).status_code == 404
    job_id = await create_job()
    assert (await client.get(f"/api/v1/jobs/{job_id}/detail-fields")).status_code == 422
    assert (await client.get(f"/api/v1/jobs/{job_id}/detail-fields", params={"fields": " , "})).status_code == 422
    assert (await client.get(f"/api/v1/jobs/{job_id}/detail-fields", params={"fields": ",".join(f"f{i}" for i in range(51))})).status_code == 422
    assert (await client.get(f"/api/v1/jobs/{job_id}/details")).status_code == 200                       # the existing route keeps its shape


async def test_every_bundled_printer_plugin_declares_or_marks_unsupported_the_minimum_fields(session_factory):
    printer_plugins = [m for m in plugins_registry.registered_plugins() if m.manufacturers]
    assert {m.id for m in printer_plugins} >= {"bambu", "elegoo_centauri", "snapmaker", "mock"}
    for m in printer_plugins:
        assert missing_min_fields(m) == [], m.id


async def test_a_printer_plugin_missing_a_minimum_field_logs_a_warning_at_load_and_still_loads(session_factory, caplog):
    with caplog.at_level(logging.WARNING, logger="app"):
        register(printer_manifest("gappy_prn", fields=("print_start_time",)), printer_manifest("full_prn", fields=PRINTER_MIN_JOB_FIELDS),
                 printer_manifest("honest_prn", unsupported=PRINTER_MIN_JOB_FIELDS))
    warned = [r.getMessage() for r in caplog.records if "minimum job field" in r.getMessage()]
    assert len(warned) == 1 and "gappy_prn" in warned[0] and "print_end_time, result" in warned[0]      # full and honest are silent
    await plugin_host.update_config("gappy_prn", enabled=True)
    assert plugin_host.is_enabled("gappy_prn") and "gappy_prn" not in plugin_host._build_errors          # the warning never blocks it


def test_the_plugin_test_kit_reports_the_minimum_job_fields_a_printer_plugin_leaves_out():
    from tests.plugins.conformance import printer_job_field_gaps
    assert printer_job_field_gaps(printer_manifest("kit_ok", fields=PRINTER_MIN_JOB_FIELDS)) == []
    assert printer_job_field_gaps(printer_manifest("kit_mixed", fields=("print_start_time",), unsupported=("result",))) == ["print_end_time"]
    assert printer_job_field_gaps(printer_manifest("kit_none")) == list(PRINTER_MIN_JOB_FIELDS)
