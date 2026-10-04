"""Printing cached versions while Laminus is down (BIZ-201).

With the slicer sidecar unavailable, a printer may still claim a job that allows cached slices when a usable cached
version matches THAT printer's slice of it; dispatch then prints exactly that version and never slices. Anything else
waits for Laminus, blocked, as before.
"""
import logging
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import Job, JobPrinterConfig, Printer
from app.services.providers.slicing import SlicingProviderError, SlicingProviderNotReady
from app.services import slice_cache
from tests.fake_providers import FakeSlicingProvider
from tests.services.test_slice_reuse import (  # noqa: F401 — `env` is a fixture
    INPUTS, _add_version, _cache_lines, _job, _seed_allowing, env,
)
from tests.services.test_slice_save import MACHINE, _engine, _run_to_printing, _seed
from tests.waiting import settle_background_tasks

UNREACHABLE = "Laminus is unreachable — slicing paused"


def _down(mode="unreachable"):
    """Make the claim's slicing-provider health probe fail the way `mode` says (the autouse fixture makes it healthy)."""
    provider = None
    if mode != "unconfigured":
        provider = FakeSlicingProvider()
        provider.fail_on["health"] = (SlicingProviderNotReady("health check returned 503") if mode == "not_ready"
                                      else SlicingProviderError("health check request failed: refused"))
    return patch("app.services.queue_engine.get_slicing_provider", return_value=provider)


async def _config(factory, job_id, printer_id=1) -> JobPrinterConfig:
    async with factory() as s:
        return (await s.execute(select(JobPrinterConfig).where(
            JobPrinterConfig.job_id == job_id, JobPrinterConfig.printer_id == printer_id))).scalar_one()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unreachable", "not_ready", "unconfigured"])
async def test_a_usable_cached_version_prints_while_laminus_is_down(session_factory, tmp_path, env, caplog, mode):
    caplog.set_level(logging.INFO, logger="app.services.slice_cache")
    version_id = await _add_version(session_factory, env)
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)

    with _down(mode):
        await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_not_called()
    job = await _job(session_factory, job_id)
    assert (job.sliced_version_id, job.block_reason) == (version_id, None)
    info = job.slice_cache_info
    assert (info["decision"], info["gate"], info["sliced_version_id"]) == ("hit", "laminus_down", version_id)
    (line,) = _cache_lines(caplog, "gate_bypass")
    assert f"cache_key={slice_cache.cache_key(INPUTS)}" in line.getMessage()
    assert f"sliced_version_id={version_id}" in line.getMessage() and "printer_id=1" in line.getMessage()


@pytest.mark.asyncio
async def test_without_a_cached_version_the_job_waits_for_laminus(session_factory, tmp_path, env):
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)

    with _down():
        await qe._process_queue()
        await settle_background_tasks()

    qe._slicer.slice.assert_not_called()
    job = await _job(session_factory, job_id)
    assert (job.status, job.block_reason, job.assigned_printer_id) == ("blocked", UNREACHABLE, None)
    assert (await _config(session_factory, job_id)).slice_failed is False


@pytest.mark.asyncio
async def test_a_job_that_does_not_allow_the_cache_waits_even_with_a_version(session_factory, tmp_path, env, caplog):
    caplog.set_level(logging.INFO, logger="app.services.slice_cache")
    await _add_version(session_factory, env)
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed(session_factory, save=False)   # allow_cached_slice off

    with _down():
        await qe._process_queue()
        await settle_background_tasks()

    qe._slicer.slice.assert_not_called()
    job = await _job(session_factory, job_id)
    assert (job.status, job.block_reason, job.sliced_version_id) == ("blocked", UNREACHABLE, None)
    assert _cache_lines(caplog, "gate_bypass") == []   # never claimed on the cache in the first place


@pytest.mark.asyncio
async def test_a_stale_version_never_carries_the_claim_under_use_latest(session_factory, tmp_path, env, caplog):
    """Laminus reachable but not ready, so staleness is KNOWN: a version dispatch would reslice must not be claimed —
    or it would be claimed and released again every cycle."""
    caplog.set_level(logging.INFO, logger="app.services.slice_cache")
    await _add_version(session_factory, env, preset_hash="old-presets")   # FINGERPRINT says "presethash"
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)

    with _down("not_ready"):
        for _ in range(3):
            await qe._process_queue()
            await settle_background_tasks()

    qe._slicer.slice.assert_not_called()
    assert _cache_lines(caplog, "gate_bypass") == []
    job = await _job(session_factory, job_id)
    assert (job.status, job.block_reason) == ("blocked", "Laminus is not ready — slicing paused")


@pytest.mark.asyncio
async def test_a_cached_file_whose_bytes_changed_does_not_carry_the_claim(session_factory, tmp_path, env):
    await _add_version(session_factory, env)
    (env / "Prints" / "Benchy cached.gcode").write_bytes(b"; edited by hand, longer than before\n")
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)

    with _down():
        await qe._process_queue()
        await settle_background_tasks()

    job = await _job(session_factory, job_id)
    assert (job.status, job.block_reason) == ("blocked", UNREACHABLE)


@pytest.mark.asyncio
async def test_an_offline_printer_parks_the_cached_version_as_sliced(session_factory, tmp_path, env):
    """Slice-ahead (printer offline) during an outage: the cached version is staged and parked, nothing sliced."""
    version_id = await _add_version(session_factory, env)
    qe, mgr = _engine(session_factory, tmp_path)
    mgr.is_printer_ready.side_effect = lambda pid: False
    job_id = await _seed_allowing(session_factory)

    with _down():
        await qe._process_queue()
        await settle_background_tasks()

    qe._slicer.slice.assert_not_called()
    job = await _job(session_factory, job_id)
    assert (job.status, job.sliced_version_id) == ("sliced", version_id)


@pytest.mark.asyncio
async def test_only_the_printer_with_a_version_claims_it(session_factory, tmp_path, env):
    """Per printer: printer 1 (another machine preset — no version for its slice) leaves the job; printer 2 (the
    preset the version was sliced for) takes it."""
    version_id = await _add_version(session_factory, env)
    qe, mgr = _engine(session_factory, tmp_path)
    mgr.get_all_printer_ids.return_value = [1, 2]
    mgr.is_printer_ready.side_effect = lambda pid: pid in (1, 2)
    job_id = await _seed_allowing(session_factory)
    async with session_factory() as s:
        (await s.get(Printer, 1)).current_orca_printer_profile = "Some Other Machine"
        s.add(Printer(id=2, name="P2", printer_type="elegoo_centauri", connection_config={},
                      current_orca_printer_profile=MACHINE,
                      loaded_filaments=[{"slot": 0, "type": "PETG", "color": "#000000",
                                         "filament_profile": "Generic PETG"}]))
        await s.flush()
        s.add(JobPrinterConfig(job_id=job_id, printer_id=2, print_profile="0.20mm Standard",
                               filament_type="PETG", filament_color="#000000"))
        await s.commit()

    with _down():
        await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_not_called()
    job = await _job(session_factory, job_id)
    assert (job.assigned_printer_id, job.sliced_version_id) == (2, version_id)
    assert (await _config(session_factory, job_id, 1)).slice_failed is False


@pytest.mark.asyncio
async def test_a_version_gone_by_dispatch_blocks_without_slicing_and_recovers(session_factory, tmp_path, env):
    """The gate saw the version, but it vanished before dispatch: the job goes back blocked — not slice_failed, not
    sliced — and prints normally once Laminus is back."""
    version_id = await _add_version(session_factory, env)
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)
    async with session_factory() as s:   # as the claim leaves it
        job = await s.get(Job, job_id)
        job.status, job.assigned_printer_id = "slicing", 1
        await s.commit()
    (env / "Prints" / "Benchy cached.gcode").unlink()

    with _down():
        await qe._run_slice_and_print(job_id, 1, 1, cache_only_version=version_id)

    qe._slicer.slice.assert_not_called()
    job = await _job(session_factory, job_id)
    assert (job.status, job.block_reason, job.assigned_printer_id, job.sliced_version_id) == (
        "blocked", UNREACHABLE, None, None)
    assert (job.slice_cache_info["decision"], job.slice_cache_info["gate"]) == ("miss", "laminus_down")
    assert (await _config(session_factory, job_id)).slice_failed is False

    await _run_to_printing(qe, session_factory, job_id)   # Laminus healthy again (autouse fixture)
    qe._slicer.slice.assert_called_once()


@pytest.mark.asyncio
async def test_laminus_back_by_dispatch_slices_as_usual(session_factory, tmp_path, env):
    """The version vanished, but Laminus recovered between claim and dispatch: no reason to wait — slice."""
    version_id = await _add_version(session_factory, env)
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.status, job.assigned_printer_id = "slicing", 1
        await s.commit()
    (env / "Prints" / "Benchy cached.gcode").unlink()

    await qe._run_slice_and_print(job_id, 1, 1, cache_only_version=version_id)   # healthy (autouse fixture)
    await settle_background_tasks()

    qe._slicer.slice.assert_called_once()
    assert (await _job(session_factory, job_id)).status == "printing"


@pytest.mark.asyncio
async def test_dispatch_prints_the_version_the_gate_chose(session_factory, tmp_path, env):
    """Two versions match the key; the claim pinned the older one — dispatch must print that one, not re-pick."""
    older = await _add_version(session_factory, env, name="older.gcode")
    await _add_version(session_factory, env, name="newer.gcode")
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.status, job.assigned_printer_id = "slicing", 1
        await s.commit()

    with _down():
        await qe._run_slice_and_print(job_id, 1, 1, cache_only_version=older)
    await settle_background_tasks()

    assert (await _job(session_factory, job_id)).sliced_version_id == older


@pytest.mark.asyncio
async def test_laminus_up_is_unchanged_no_gate_tag(session_factory, tmp_path, env, caplog):
    caplog.set_level(logging.INFO, logger="app.services.slice_cache")
    await _add_version(session_factory, env)
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)

    await _run_to_printing(qe, session_factory, job_id)

    assert "gate" not in (await _job(session_factory, job_id)).slice_cache_info
    assert _cache_lines(caplog, "gate_bypass") == []


@pytest.mark.asyncio
async def test_a_failing_claim_check_never_claims(session_factory, tmp_path, env):
    await _add_version(session_factory, env)
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)

    with _down(), patch.object(qe, "_usable_version", side_effect=RuntimeError("boom")):
        await qe._process_queue()
        await settle_background_tasks()

    job = await _job(session_factory, job_id)
    assert (job.status, job.block_reason) == ("blocked", UNREACHABLE)
