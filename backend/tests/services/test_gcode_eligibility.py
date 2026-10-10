"""Machine eligibility for pre-sliced G-code (BIZ-263): the decision, slice-target recording, equivalents, queue outcomes."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app import plugins
from app.models import FileMachineEligibility, Job, JobPrinterConfig, Printer, UploadedFile
from app.plugins import PluginError
from app.plugins.manifest import Manufacturer, PrinterModel
from app.services import gcode_eligibility as eligibility
from app.services import printer_model_registry as registry
from app.services.queue_engine import QueueEngine
from tests.plugins.dummy_plugin import make_manifest
from tests.services.test_queue_engine import _install_fake_put, _make_mock_printer_manager
from tests.waiting import settle_background_tasks, wait_until

GCODE = b"; filament used [g] = 3.5\n; estimated printing time (normal mode) = 10m 5s\nG28\n"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture(autouse=True)
def _fake_plugin():
    saved = dict(plugins._REGISTRY)
    plugins.register_plugin(make_manifest("fake_printers", default_enabled=True, manufacturers=(
        Manufacturer("acme", "Acme", (PrinterModel("x1", "X1", equivalents=("x1_pro",)), PrinterModel("x1_pro", "X1 Pro"),
                                      PrinterModel("x2", "X2"))),)))
    yield
    plugins._REGISTRY.clear()
    plugins._REGISTRY.update(saved)


async def seed_models(factory) -> dict[str, str]:
    async with factory() as s:
        await registry.sync_registry(s)
        return {m["model_id"]: m["id"] for m in await registry.list_models(s, plugin_id="fake_printers")}


async def seed_file(factory, library, name="part.gcode", *, known_for: list[str] | None = None) -> int:
    library.mkdir(parents=True, exist_ok=True)
    (library / name).write_bytes(GCODE)
    async with factory() as s:
        f = UploadedFile(original_filename=name, relative_path=name, folder="/", plates=[], uploaded_at=_now())
        s.add(f)
        await s.flush()
        if known_for is not None:
            await eligibility.set_eligibility(s, f, known_for)
        await s.commit()
        return f.id


async def seed_printer(factory, pid: int, model_uuid: str | None) -> None:
    async with factory() as s:
        s.add(Printer(id=pid, name=f"P{pid}", printer_type="elegoo_centauri", connection_config={}, model_uuid=model_uuid))
        await s.commit()


async def verdict(factory, file_id: int, pid: int, confirmed: bool = False) -> eligibility.Outcome:
    async with factory() as s:
        return await eligibility.evaluate(s, await s.get(UploadedFile, file_id), await s.get(Printer, pid), confirmed=confirmed)


# --- the decision ----------------------------------------------------------------------------------------------

async def test_a_model_file_is_not_subject_to_machine_eligibility(session_factory, tmp_path):
    models = await seed_models(session_factory)
    await seed_printer(session_factory, 1, models["x1"])
    async with session_factory() as s:
        f = UploadedFile(original_filename="m.3mf", relative_path="m.3mf", folder="/", plates=[], uploaded_at=_now())
        s.add(f)
        await s.commit()
        fid = f.id

    assert (await verdict(session_factory, fid, 1)).code == "not_applicable"


async def test_eligible_incompatible_unknown_and_unregistered_printer_each_have_an_explicit_outcome(session_factory, tmp_path):
    models = await seed_models(session_factory)
    await seed_printer(session_factory, 1, models["x1"])
    await seed_printer(session_factory, 2, models["x2"])
    await seed_printer(session_factory, 3, None)
    known = await seed_file(session_factory, tmp_path / "lib", "known.gcode", known_for=[models["x1"]])
    legacy = await seed_file(session_factory, tmp_path / "lib", "legacy.gcode")

    ok = await verdict(session_factory, known, 1)
    bad = await verdict(session_factory, known, 2)
    nomodel = await verdict(session_factory, known, 3)
    unknown = await verdict(session_factory, legacy, 1)
    confirmed = await verdict(session_factory, legacy, 1, confirmed=True)

    assert (ok.allowed, ok.code) == (True, "eligible")
    assert (bad.allowed, bad.code) == (False, "incompatible") and "P2" in bad.message and "Acme X1" in bad.message
    assert (nomodel.allowed, nomodel.code) == (False, "printer_model_unknown")
    assert (unknown.allowed, unknown.code) == (False, "unknown_eligibility") and "confirm" in unknown.message
    assert (confirmed.allowed, confirmed.code) == (True, "unknown_confirmed")


async def test_manual_eligibility_never_widens_to_equivalents_but_a_recorded_slice_does(session_factory, tmp_path):
    models = await seed_models(session_factory)
    await seed_printer(session_factory, 1, models["x1_pro"])
    await seed_printer(session_factory, 2, models["x2"])
    manual = await seed_file(session_factory, tmp_path / "lib", "manual.gcode", known_for=[models["x1"]])
    sliced = await seed_file(session_factory, tmp_path / "lib", "sliced.gcode")
    async with session_factory() as s:
        assert await eligibility.record_slice_target(s, await s.get(UploadedFile, sliced), models["x1"]) is True
        await s.commit()

    assert (await verdict(session_factory, manual, 1)).code == "incompatible"      # only what the user ticked
    equiv = await verdict(session_factory, sliced, 1)
    assert (equiv.allowed, equiv.code) == (True, "equivalent")                       # declared equivalent of the sliced target
    assert (await verdict(session_factory, sliced, 2)).code == "incompatible"        # x2 is not declared equivalent: never inferred
    async with session_factory() as s:
        rows = (await s.execute(select(FileMachineEligibility).where(FileMachineEligibility.file_id == sliced))).scalars().all()
    assert {(r.model_uuid, r.source) for r in rows} == {(models["x1"], "target"), (models["x1_pro"], "equivalent")}


async def test_equivalence_is_symmetric_from_either_side(session_factory):
    models = await seed_models(session_factory)
    async with session_factory() as s:
        assert await eligibility.equivalents_of(s, models["x1_pro"]) == {models["x1"]}
        assert await eligibility.equivalents_of(s, models["x1"]) == {models["x1_pro"]}
        assert await eligibility.equivalents_of(s, models["x2"]) == set()


async def test_a_slice_from_a_printer_without_a_registered_model_leaves_the_file_unknown(session_factory, tmp_path):
    await seed_models(session_factory)
    fid = await seed_file(session_factory, tmp_path / "lib", "s.gcode")
    async with session_factory() as s:
        assert await eligibility.record_slice_target(s, await s.get(UploadedFile, fid), None) is False
        await s.commit()
        assert (await s.get(UploadedFile, fid)).eligibility_known is False


async def test_set_eligibility_can_represent_zero_one_and_several_models_and_rejects_bad_input(session_factory, tmp_path):
    models = await seed_models(session_factory)
    fid = await seed_file(session_factory, tmp_path / "lib", "s.gcode")
    async with session_factory() as s:
        f = await s.get(UploadedFile, fid)
        assert f.eligibility_known is False                                          # unknown is the default, not "zero"
        await eligibility.set_eligibility(s, f, [])
        assert (f.eligibility_known, (await eligibility.eligibility_of(s, fid))["models"]) == (True, [])
        await eligibility.set_eligibility(s, f, [models["x1"]])
        await eligibility.set_eligibility(s, f, [models["x1"], models["x2"], models["x1"]])
        assert {m["model_uuid"] for m in (await eligibility.eligibility_of(s, fid))["models"]} == {models["x1"], models["x2"]}
        with pytest.raises(ValueError):
            await eligibility.set_eligibility(s, f, ["no-such-model"])
        other = UploadedFile(original_filename="m.3mf", relative_path="m.3mf", folder="/", plates=[], uploaded_at=_now())
        s.add(other)
        await s.flush()
        with pytest.raises(ValueError):
            await eligibility.set_eligibility(s, other, [models["x1"]])


def test_a_manifest_may_only_declare_equivalents_that_exist_in_the_plugin():
    with pytest.raises(PluginError):
        make_manifest("fake_two", manufacturers=(Manufacturer("acme", "Acme", (PrinterModel("x1", "X1", equivalents=("ghost",)),)),))
    with pytest.raises(PluginError):
        make_manifest("fake_two", manufacturers=(Manufacturer("acme", "Acme", (PrinterModel("x1", "X1", equivalents=("x1",)),)),))


# --- the queue pull ---------------------------------------------------------------------------------------------

async def _engine(session_factory, tmp_path, monkeypatch, printer_ids):
    monkeypatch.setenv("THEMIS_LIBRARY_DIR", str(tmp_path / "library"))
    mgr = _make_mock_printer_manager(printer_ids)
    slicer = MagicMock()
    slicer._data_dir = tmp_path / "data"
    qe = QueueEngine(session_factory, mgr, slicer)
    _install_fake_put(qe)
    return qe, mgr, slicer


async def _queue_job(factory, file_id, pid, *, confirmed=False) -> int:
    async with factory() as s:
        j = Job(uploaded_file_id=file_id, plate_number=1, queue_position=1.0, status="queued", eligibility_confirmed=confirmed,
                created_at=_now(), updated_at=_now())
        s.add(j)
        await s.flush()
        s.add(JobPrinterConfig(job_id=j.id, printer_id=pid, print_profile="", filament_type="any", filament_color="any"))
        await s.commit()
        return j.id


async def _status(factory, job_id) -> tuple[str, str | None]:
    async with factory() as s:
        j = await s.get(Job, job_id)
        return j.status, j.block_reason


@pytest.mark.parametrize("printer_model,file_for,confirmed,expect_print", [
    ("x1", ["x1"], False, True),          # eligible model
    ("x1_pro", None, False, False),       # filled below: unknown legacy file, unconfirmed
])
async def test_queue_pull_prints_an_eligible_job(session_factory, tmp_path, monkeypatch, printer_model, file_for, confirmed, expect_print):
    models = await seed_models(session_factory)
    qe, mgr, slicer = await _engine(session_factory, tmp_path, monkeypatch, [1])
    await seed_printer(session_factory, 1, models[printer_model])
    fid = await seed_file(session_factory, tmp_path / "library", known_for=[models[m] for m in file_for] if file_for else None)
    jid = await _queue_job(session_factory, fid, 1, confirmed=confirmed)

    await qe._process_queue()
    await settle_background_tasks()

    status, reason = await _status(session_factory, jid)
    if expect_print:
        await wait_until(lambda: _is_printing(session_factory, jid), what="eligible gcode job to print")
        slicer.slice.assert_not_called()
    else:
        assert status == "blocked" and "no recorded machine eligibility" in reason


async def _is_printing(factory, jid) -> bool:
    return (await _status(factory, jid))[0] == "printing"


async def test_queue_pull_blocks_an_incompatible_model_with_a_user_visible_reason_and_sends_nothing(session_factory, tmp_path, monkeypatch):
    models = await seed_models(session_factory)
    qe, mgr, slicer = await _engine(session_factory, tmp_path, monkeypatch, [1])
    await seed_printer(session_factory, 1, models["x2"])
    fid = await seed_file(session_factory, tmp_path / "library", known_for=[models["x1"]])
    jid = await _queue_job(session_factory, fid, 1)

    await qe._process_queue()
    await settle_background_tasks()

    status, reason = await _status(session_factory, jid)
    assert status == "blocked" and "P1 can't take part.gcode" in reason and "Acme X1" in reason
    mgr.get_client.return_value.start_print.assert_not_called()


async def test_queue_pull_prints_on_a_registry_declared_equivalent_model(session_factory, tmp_path, monkeypatch):
    models = await seed_models(session_factory)
    qe, mgr, slicer = await _engine(session_factory, tmp_path, monkeypatch, [1])
    await seed_printer(session_factory, 1, models["x1_pro"])
    fid = await seed_file(session_factory, tmp_path / "library")
    async with session_factory() as s:
        await eligibility.record_slice_target(s, await s.get(UploadedFile, fid), models["x1"])
        await s.commit()
    jid = await _queue_job(session_factory, fid, 1)

    await qe._process_queue()

    await wait_until(lambda: _is_printing(session_factory, jid), what="equivalent-model job to print")


async def test_queue_pull_prints_a_confirmed_unknown_file_and_blocks_it_unconfirmed(session_factory, tmp_path, monkeypatch):
    models = await seed_models(session_factory)
    qe, mgr, slicer = await _engine(session_factory, tmp_path, monkeypatch, [1])
    await seed_printer(session_factory, 1, models["x1"])
    fid = await seed_file(session_factory, tmp_path / "library")
    blocked = await _queue_job(session_factory, fid, 1)

    await qe._process_queue()
    await settle_background_tasks()
    assert (await _status(session_factory, blocked))[0] == "blocked"

    async with session_factory() as s:
        (await s.get(Job, blocked)).eligibility_confirmed = True          # the user confirms; the blocked job is retried
        await s.commit()
    await qe._process_queue()
    await wait_until(lambda: _is_printing(session_factory, blocked), what="confirmed unknown-eligibility job to print")


async def test_dispatching_gcode_never_touches_the_slicing_provider_even_with_laminus_unavailable(session_factory, tmp_path, monkeypatch):
    models = await seed_models(session_factory)
    qe, mgr, slicer = await _engine(session_factory, tmp_path, monkeypatch, [1])
    await seed_printer(session_factory, 1, models["x1"])
    fid = await seed_file(session_factory, tmp_path / "library", known_for=[models["x1"]])
    jid = await _queue_job(session_factory, fid, 1)

    def boom(*a, **k):
        raise AssertionError("the slicing provider must not be consulted for pre-sliced G-code")

    with patch("app.services.queue_engine.get_slicing_provider", side_effect=boom), \
            patch.object(QueueEngine, "_laminus_down_reason", side_effect=boom):
        await qe._process_queue()
        await wait_until(lambda: _is_printing(session_factory, jid), what="gcode job to print with Laminus unavailable")
    slicer.slice.assert_not_called()
