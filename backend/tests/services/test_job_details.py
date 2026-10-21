"""Job details (spec §3): open field names and values, a status instead of an error for what a source cannot provide, a manifest that
declares fields, and a single transaction with the caller."""
import pytest
from sqlalchemy import select

from app.models import Job, JobDetail, Printer, UploadedFile
from app.plugins import JobFieldDef, PluginError
from app.plugins.host import plugin_host
from app.plugins.manifest import HOST_API, PluginManifest
from app.services import job_details as jd
from app.services.job_details import UNSUPPORTED, get_details, set_details
from tests.eventing.fakes import register
from tests.plugins.dummy_plugin import DummyProvider, DummySettings

NOW = "2026-10-20T12:00:00Z"


def manifest(pid: str, *, fields=(), unsupported=()) -> PluginManifest:
    return PluginManifest(id=pid, name=pid, version="1.0.0", host_api=HOST_API, settings_model=DummySettings, factory=DummyProvider,
                          job_fields=tuple(JobFieldDef(f, description=f"{f} desc", type_hint="x") for f in fields),
                          unsupported_job_fields=tuple(unsupported))


async def make_job(session_factory, plugin_id: str | None = None, *, assigned: bool = True) -> int:
    async with session_factory() as s:
        printer = Printer(name="P", printer_type="bambu", connection_config={}, orca_printer_profiles=[], plugin_id=plugin_id)
        f = UploadedFile(original_filename="m.3mf", stored_path="/x", plates=[], uploaded_at=NOW)
        s.add_all([printer, f])
        await s.flush()
        job = Job(uploaded_file_id=f.id, plate_number=1, queue_position=1.0, status="printing", created_at=NOW, updated_at=NOW,
                  assigned_printer_id=printer.id if assigned else None, printed_on_printer_id=printer.id)
        s.add(job)
        await s.commit()
        return job.id


async def write(session_factory, job_id, origin, details):
    async with session_factory() as s:
        await set_details(s, job_id, origin, details)
        await s.commit()


async def test_a_mixed_request_answers_each_field_with_its_own_status(session_factory):
    register(manifest("prn_a", fields=("print_end_time", "filament_usage"), unsupported=("filament_used_grams",)))
    await plugin_host.update_config("prn_a", enabled=True)
    job = await make_job(session_factory, "prn_a")
    await write(session_factory, job, "prn_a", {"print_end_time": "2026-10-20T13:00:00Z"})

    got = await get_details(job, ["print_end_time", "filament_used_grams", "filament_usage", "no_such_field"])

    assert got["print_end_time"] == {"status": "ok", "value": "2026-10-20T13:00:00Z", "origin": "prn_a", "updated_at": got["print_end_time"]["updated_at"]}
    assert got["print_end_time"]["updated_at"] is not None
    assert got["filament_used_grams"]["status"] == "unsupported" and got["filament_used_grams"]["origin"] == "prn_a"
    assert got["filament_usage"]["status"] == "unavailable"          # declared, but this job has no value
    assert got["no_such_field"] == {"status": "unknown_field", "value": None, "origin": None, "updated_at": None}


async def test_a_runtime_unsupported_marker_is_stored_and_returned(session_factory):
    register(manifest("prn_b", fields=("filament_used_grams",)))
    await plugin_host.update_config("prn_b", enabled=True)
    job = await make_job(session_factory, "prn_b")
    await write(session_factory, job, "prn_b", {"filament_used_grams": UNSUPPORTED})

    got = (await get_details(job, ["filament_used_grams"]))["filament_used_grams"]

    assert (got["status"], got["value"], got["origin"]) == ("unsupported", None, "prn_b")
    async with session_factory() as s:
        row = (await s.execute(select(JobDetail))).scalar_one()
    assert row.status == "unsupported"


async def test_any_field_name_and_json_value_is_stored_and_read_back(session_factory):
    job = await make_job(session_factory, None)
    value = {"slots": [{"i": 0, "g": 12.5}, {"i": 1, "g": None}], "note": "ünïcode", "nested": {"a": [1, 2, 3]}}
    await write(session_factory, job, "anyone", {"totally.made_up-name": value, "n": 0, "empty": ""})

    got = await get_details(job, ["totally.made_up-name", "n", "empty"])

    assert got["totally.made_up-name"]["value"] == value and got["totally.made_up-name"]["status"] == "ok"
    assert got["n"]["value"] == 0 and got["empty"]["value"] == ""            # falsy values are values, not "missing"


async def test_a_rewrite_updates_the_row_instead_of_adding_one(session_factory):
    job = await make_job(session_factory, None)
    await write(session_factory, job, "o", {"f": 1})
    await write(session_factory, job, "o", {"f": 2})
    async with session_factory() as s:
        rows = (await s.execute(select(JobDetail))).scalars().all()
    assert [(r.field, r.value) for r in rows] == [("f", 2)]


async def test_details_stay_readable_after_the_printer_forgets_the_job(session_factory):
    register(manifest("prn_c", fields=("result",)))
    await plugin_host.update_config("prn_c", enabled=True)
    job = await make_job(session_factory, "prn_c")
    await write(session_factory, job, "prn_c", {"result": "success"})
    async with session_factory() as s:                                         # the job leaves the printer (a finished, unassigned job)
        (await s.get(Job, job)).assigned_printer_id = None
        await s.commit()

    assert (await get_details(job, ["result"]))["result"]["value"] == "success"


async def test_with_two_origins_the_printer_plugin_wins_and_origin_selects_the_other(session_factory):
    register(manifest("prn_d", fields=("cost",)), manifest("other_d", fields=("cost",)))
    await plugin_host.update_config("prn_d", enabled=True)
    await plugin_host.update_config("other_d", enabled=True)
    job = await make_job(session_factory, "prn_d")
    await write(session_factory, job, "prn_d", {"cost": 1})
    await write(session_factory, job, "other_d", {"cost": 2})                  # written later, but not by the printer plugin

    assert (await get_details(job, ["cost"]))["cost"]["value"] == 1
    assert (await get_details(job, ["cost"], origin="other_d"))["cost"]["value"] == 2
    assert (await get_details(job, ["cost"], origin="nobody"))["cost"]["status"] == "unknown_field"


async def test_an_unknown_job_never_raises(session_factory):
    got = await get_details(999999, ["a", "b"])
    assert {k: v["status"] for k, v in got.items()} == {"a": "unknown_field", "b": "unknown_field"}


async def test_details_and_the_callers_state_change_commit_or_roll_back_together(session_factory):
    job = await make_job(session_factory, None)
    async with session_factory() as s:
        (await s.get(Job, job)).status = "complete"
        await set_details(s, job, "o", {"result": "success"})
        await s.rollback()
    async with session_factory() as s:
        assert (await s.get(Job, job)).status == "printing"
        assert (await s.execute(select(JobDetail))).first() is None


async def test_the_field_catalog_lists_enabled_plugins_only(session_factory):
    register(manifest("prn_e", fields=("fe_field",), unsupported=("fe_unsupported",)))
    assert "fe_field" not in jd.field_catalog()                                # registered but not enabled
    await plugin_host.update_config("prn_e", enabled=True)
    cat = jd.field_catalog()
    assert cat["fe_field"] == [{"plugin_id": "prn_e", "description": "fe_field desc", "type_hint": "x", "supported": True}]
    assert cat["fe_unsupported"][0]["supported"] is False
    await plugin_host.update_config("prn_e", enabled=False)
    assert "fe_field" not in jd.field_catalog()


def test_manifest_job_field_declarations_are_validated():
    with pytest.raises(PluginError, match="both declared and marked unsupported"):
        manifest("bad_one", fields=("a",), unsupported=("a",))
    with pytest.raises(PluginError, match="unique"):
        manifest("bad_two", fields=("a", "a"))
    with pytest.raises(PluginError, match="unique"):
        manifest("bad_three", fields=("Bad Name",))
    with pytest.raises(PluginError, match="not a valid field name"):
        manifest("bad_four", unsupported=("Bad Name",))
    assert manifest("good_one", fields=("filament.cost", "print_end_time")).job_fields[0].name == "filament.cost"
