"""Preset overrides for sliced jobs: the 3MF's own preset changes are carried onto the routed printer's preset, and
job > 3MF > printer when they conflict. Core sends the job's input (`extra_config`) and the printer's overrides
(`printer_overrides`) separately; the provider reads the 3MF and merges (see test_laminus_producer.py for that merge)."""
import io
import json
import zipfile

import pytest

from app.models import Job, Printer
from app.services import slice_cache, slice_overrides
from app.services.providers.laminus.overrides import embedded_process_overrides
from app.services.slicer_service import SliceRequest, _settings_of
from tests.services.test_slice_save import _engine, _run_to_printing, _seed, env  # noqa: F401  (env is a fixture)


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


def test_unsafe_carried_keys_are_dropped(tmp_path):
    """A post-processing script would make the Orca CLI refuse the slice on every printer; wipe-tower positions belong to
    the original bed; a per-extruder (multi-value) setting doesn't fit another printer's extruder count; a filament index
    (support interface on filament 2) isn't rewritten by a tool remap and may not exist on the target."""
    path = tmp_path / "m.3mf"
    path.write_bytes(_orca_3mf(
        {"post_process": "/usr/bin/script.py", "filename_format": "{input_filename_base}.gcode", "gcode_substitutions": ["a"],
         "wipe_tower_x": ["165"], "wipe_tower_y": ["250"], "outer_wall_speed": ["200", "180"], "enable_support": "1",
         "support_interface_filament": "2", "wall_filament": "3"},
        process_diff=["post_process", "filename_format", "gcode_substitutions", "wipe_tower_x", "wipe_tower_y",
                      "outer_wall_speed", "enable_support", "support_interface_filament", "wall_filament"]))
    assert embedded_process_overrides(path) == {"enable_support": "1"}


# ---- precedence (the preview the New Job check uses) ----------------------------------------------------------------

# The examples: the job picks "enable supports" and a brim type, the printer always wants an outer brim, the 3MF has
# its own brim/support choices. Job > 3MF > printer.
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


def test_clean_keeps_only_curated_non_blank_keys_as_strings():
    assert slice_overrides.clean({"brim_type": "outer_only", "enable_support": 1, "post_process": "rm -rf /"}) == \
        {"brim_type": "outer_only", "enable_support": "1"}
    assert slice_overrides.clean({"post_process": "x"}) is None and slice_overrides.clean(None) is None
    assert slice_overrides.clean({"brim_width": " ", "wall_loops": None, "layer_height": " 0.2 "}) == {"layer_height": "0.2"}


class _File:
    def __init__(self, name):
        self.relative_path, self.original_filename, self.content_hash, self.mtime = f"Prints/{name}", name, "h", 1.0


async def test_embedded_for_reads_a_3mf_and_skips_gcode_missing_and_broken_files(env, monkeypatch):  # noqa: F811
    (env / "Prints" / "m.3mf").write_bytes(_orca_3mf({"brim_type": "no_brim"}, ["brim_type"]))
    assert await slice_overrides.embedded_for(_File("m.3mf")) == {"brim_type": "no_brim"}
    (env / "Prints" / "m.gcode").write_bytes(b"G28\n")
    assert await slice_overrides.embedded_for(_File("m.gcode")) == {}
    assert await slice_overrides.embedded_for(_File("gone.3mf")) == {}
    assert await slice_overrides.embedded_for(None) == {}
    monkeypatch.setattr(slice_overrides, "_read_embedded", lambda f: (_ for _ in ()).throw(OSError("store hiccup")))
    assert await slice_overrides.embedded_for(_File("m.3mf")) == {}


# ---- what core sends the provider ------------------------------------------------------------------------------

def _req(**kw) -> SliceRequest:
    return SliceRequest(job_id=1, source_3mf="", plate_number=1, machine_preset="M", process_preset="P",
                        filament_presets=["F"], **kw)


def test_the_neutral_settings_keep_the_printers_overrides_apart_from_the_jobs():
    s = _settings_of(_req(extra_config={"curr_bed_type": "Textured PEI Plate", "enable_support": "1"},
                          printer_overrides={"brim_type": "outer_only"}))
    assert (s.plate_type, dict(s.overrides), dict(s.printer_overrides)) == \
        ("Textured PEI Plate", {"enable_support": "1"}, {"brim_type": "outer_only"})


def test_the_cache_key_changes_with_the_printers_overrides_and_is_unchanged_without_any():
    plain = slice_cache.key_inputs(_req(), "hash", None, None)
    assert "printer_overrides" not in plain.as_dict()                     # keys of printers without overrides don't move
    brim = slice_cache.key_inputs(_req(printer_overrides={"brim_type": "outer_only"}), "hash", None, None)
    job_brim = slice_cache.key_inputs(_req(extra_config={"brim_type": "outer_only"}), "hash", None, None)
    # the same value from the printer and from the job can slice differently (the 3MF sits between them)
    assert len({slice_cache.cache_key(plain), slice_cache.cache_key(brim), slice_cache.cache_key(job_brim)}) == 3


async def _seed_example(session_factory, env, **seed):  # noqa: F811
    (env / "Prints" / "Benchy.3mf").write_bytes(_orca_3mf({"brim_type": "no_brim"}, ["brim_type"]))
    job_id = await _seed(session_factory, **seed)
    async with session_factory() as s:
        p = await s.get(Printer, 1)
        p.build_plate_type = "Textured PEI Plate"
        p.slice_overrides = {"brim_type": "outer_only", "support_type": "tree(auto)"}
        (await s.get(Job, job_id)).overrides = {"enable_support": "1"}
        await s.commit()
    return job_id


async def test_dispatch_sends_the_jobs_input_and_the_printers_overrides_separately(session_factory, tmp_path, env):  # noqa: F811
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_example(session_factory, env, save=False)

    await _run_to_printing(qe, session_factory, job_id)

    req = qe._slicer.slice.call_args.args[0]
    assert req.extra_config == {"curr_bed_type": "Textured PEI Plate", "enable_support": "1"}
    assert req.printer_overrides == {"brim_type": "outer_only", "support_type": "tree(auto)"}


async def test_the_estimate_slices_with_the_same_overrides(session_factory, tmp_path, env):  # noqa: F811
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_example(session_factory, env, save=False)
    async with session_factory() as s:
        j = await s.get(Job, job_id)
        j.estimate_status, j.estimate_token = "pending", 1
        await s.commit()

    await qe.run_estimate(job_id)

    req = qe._slicer.slice.call_args.args[0]
    assert req.extra_config == {"curr_bed_type": "Textured PEI Plate", "enable_support": "1"}
    assert req.printer_overrides == {"brim_type": "outer_only", "support_type": "tree(auto)"}
