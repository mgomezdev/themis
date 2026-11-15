"""Preset overrides for sliced jobs: the 3MF's own preset changes are carried onto the routed printer's preset, and
job > 3MF > printer when they conflict (services/slice_overrides)."""
import io
import json
import zipfile

import pytest
from sqlalchemy import select

from app.models import Job, Printer
from app.services import slice_overrides
from app.services.providers.laminus.overrides import embedded_process_overrides
from tests.services.test_slice_save import _engine, _run_to_printing, _seed, env  # noqa: F401  (env is a fixture)


@pytest.fixture(autouse=True)
def _fresh_memo():
    slice_overrides._EMBEDDED_MEMO.clear()
    yield
    slice_overrides._EMBEDDED_MEMO.clear()


def _orca_3mf(settings: dict, process_diff: list[str], filament_diff: list[str] = (), printer_diff: list[str] = ()) -> bytes:
    """A 3MF as Orca/Bambu write it: `different_settings_to_system` = [process, filament..., printer] key lists."""
    ps = {**settings, "different_settings_to_system": [";".join(process_diff), ";".join(filament_diff), ";".join(printer_diff)]}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps(ps))
    return buf.getvalue()


# ---- reading the 3MF's own changes --------------------------------------------------------------------------------

def test_only_the_process_presets_changes_are_carried_with_the_files_values(tmp_path):
    path = tmp_path / "m.3mf"
    path.write_bytes(_orca_3mf(
        {"enable_support": "1", "brim_type": "no_brim", "wall_loops": "4", "layer_height": "0.2",
         "nozzle_temperature": ["230"], "printable_area": ["0x0", "256x0"], "print_settings_id": "0.20mm Standard @BBL X1C"},
        process_diff=["enable_support", "brim_type", "wall_loops", "print_settings_id"],
        filament_diff=["nozzle_temperature"], printer_diff=["printable_area"]))

    assert embedded_process_overrides(path) == {"enable_support": "1", "brim_type": "no_brim", "wall_loops": "4"}


@pytest.mark.parametrize("settings", [
    {"enable_support": "1"},                                                     # no diff list at all: nothing to carry
    {"enable_support": "1", "different_settings_to_system": []},
    {"enable_support": "1", "different_settings_to_system": ["", "", ""]},       # author changed nothing
    {"different_settings_to_system": ["enable_support;brim_type", "", ""]},      # listed but not stored
])
def test_nothing_is_carried_when_the_file_records_no_stored_change(tmp_path, settings):
    path = tmp_path / "m.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps(settings))
    assert embedded_process_overrides(path) == {}


def test_a_bare_or_broken_file_carries_nothing(tmp_path):
    bare = tmp_path / "bare.3mf"
    with zipfile.ZipFile(bare, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
    junk = tmp_path / "junk.3mf"
    junk.write_bytes(b"not a zip")
    assert embedded_process_overrides(bare) == {} and embedded_process_overrides(junk) == {}


# ---- precedence -------------------------------------------------------------------------------------------------

# The examples: the job picks "enable supports" and a brim type, the printer always wants an outer brim, the 3MF has
# its own brim/support choices. Job > 3MF > printer; the printer's bed type sits under everything.
@pytest.mark.parametrize("printer, embedded, job, expected", [
    ({"brim_type": "outer_only"}, {"brim_type": "no_brim", "enable_support": "0"}, {"enable_support": "1", "brim_type": "brim_ears"},
     {"brim_type": "brim_ears", "enable_support": "1"}),                                  # job beats both
    ({"brim_type": "outer_only"}, {"brim_type": "no_brim"}, {"enable_support": "1"},
     {"brim_type": "no_brim", "enable_support": "1"}),                                    # 3MF beats printer
    ({"brim_type": "outer_only"}, {}, {"enable_support": "1"},
     {"brim_type": "outer_only", "enable_support": "1"}),                                 # printer fills what nobody else set
    (None, None, None, {}),
])
def test_job_beats_3mf_beats_printer(printer, embedded, job, expected):
    assert slice_overrides.effective(printer, embedded, job) == expected
    assert slice_overrides.extra_config("Textured PEI Plate", printer, embedded, job) == \
        {"curr_bed_type": "Textured PEI Plate", **expected}


def test_clean_keeps_only_curated_keys_as_strings():
    assert slice_overrides.clean({"brim_type": "outer_only", "enable_support": 1, "post_process": "rm -rf /"}) == \
        {"brim_type": "outer_only", "enable_support": "1"}
    assert slice_overrides.clean({"post_process": "x"}) is None and slice_overrides.clean(None) is None
    assert slice_overrides.clean({"brim_width": " ", "wall_loops": None, "layer_height": " 0.2 "}) == {"layer_height": "0.2"}


async def test_embedded_for_skips_gcode_and_reads_a_3mf(env):  # noqa: F811
    (env / "Prints" / "m.3mf").write_bytes(_orca_3mf({"brim_type": "no_brim"}, ["brim_type"]))

    class F:
        def __init__(self, name):
            self.relative_path, self.original_filename, self.content_hash, self.mtime = f"Prints/{name}", name, "h", 1.0
    assert await slice_overrides.embedded_for(F("m.3mf")) == {"brim_type": "no_brim"}
    (env / "Prints" / "m.gcode").write_bytes(b"G28\n")
    assert await slice_overrides.embedded_for(F("m.gcode")) == {}
    assert await slice_overrides.embedded_for(None) == {}


# ---- the real dispatch path ------------------------------------------------------------------------------------

async def _slice_config(session_factory, tmp_path, env, *, printer_overrides, job_overrides, threemf):  # noqa: F811
    (env / "Prints" / "Benchy.3mf").write_bytes(threemf)        # before seeding, so the index matches the file on disk
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed(session_factory, save=False)
    async with session_factory() as s:
        p = await s.get(Printer, 1)
        p.build_plate_type = "Textured PEI Plate"
        p.slice_overrides = printer_overrides
        (await s.get(Job, job_id)).overrides = job_overrides
        await s.commit()
    await _run_to_printing(qe, session_factory, job_id)
    return qe._slicer.slice.call_args.args[0]


async def test_dispatch_carries_the_3mf_changes_onto_the_printers_preset_under_the_job(session_factory, tmp_path, env):  # noqa: F811
    """The 3MF was made for an X1C preset; the printer that claims it slices with a different machine preset. The file's
    own changes still apply, the job's picks (supports on, brim ears) win over it, the printer's outer brim loses to both,
    and the printer's override fills a key nobody else set."""
    threemf = _orca_3mf({"enable_support": "0", "brim_type": "no_brim", "wall_loops": "4",
                         "print_settings_id": "0.20mm Standard @BBL X1C", "printer_settings_id": "Bambu Lab X1 Carbon 0.4 nozzle"},
                        process_diff=["enable_support", "brim_type", "wall_loops"])

    req = await _slice_config(session_factory, tmp_path, env,
                              printer_overrides={"brim_type": "outer_only", "support_type": "tree(auto)"},
                              job_overrides={"enable_support": "1", "brim_type": "brim_ears"}, threemf=threemf)

    assert req.machine_preset == "Test Machine Preset"           # the routed printer's preset, not the file's
    assert req.extra_config == {"curr_bed_type": "Textured PEI Plate", "support_type": "tree(auto)",
                                "enable_support": "1", "brim_type": "brim_ears", "wall_loops": "4"}


async def test_dispatch_3mf_brim_beats_the_printers_brim_when_the_job_says_nothing(session_factory, tmp_path, env):  # noqa: F811
    threemf = _orca_3mf({"brim_type": "no_brim"}, process_diff=["brim_type"])
    req = await _slice_config(session_factory, tmp_path, env, printer_overrides={"brim_type": "outer_only"},
                              job_overrides=None, threemf=threemf)
    assert req.extra_config == {"curr_bed_type": "Textured PEI Plate", "brim_type": "no_brim"}


async def test_dispatch_uses_the_printers_brim_when_neither_job_nor_3mf_sets_one(session_factory, tmp_path, env):  # noqa: F811
    threemf = _orca_3mf({"wall_loops": "3"}, process_diff=[])
    req = await _slice_config(session_factory, tmp_path, env, printer_overrides={"brim_type": "outer_only"},
                              job_overrides={"enable_support": "1"}, threemf=threemf)
    assert req.extra_config == {"curr_bed_type": "Textured PEI Plate", "brim_type": "outer_only", "enable_support": "1"}
