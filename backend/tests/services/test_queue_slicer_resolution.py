"""BIZ-291: the queue picks the slicing provider per printer (preferred plugin, else the default) and never falls back."""
import logging
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import Job, Printer
from app.services import slice_cache
from app.services.providers.slicing import SlicingProviderError
from tests.fake_providers import FakeSlicingProvider
from tests.services.test_slice_reuse import (  # noqa: F401 — `env` is a fixture
    INPUTS, _add_version, _cache_lines, _job, _seed_allowing, env,
)
from tests.services.test_slice_save import FINGERPRINT, _engine, _run_to_printing, _seed
from tests.slicing_env import slicing_providers
from tests.waiting import settle_background_tasks


def down():
    p = FakeSlicingProvider()
    p.fail_on["health"] = SlicingProviderError("health check request failed: refused")
    return p


async def _prefer(factory, plugin_id, printer_id=1):
    async with factory() as s:
        (await s.get(Printer, printer_id)).preferred_slicer_plugin_id = plugin_id
        await s.commit()


def _capture(qe):
    seen = []
    inner = qe._slicer.slice.side_effect

    def spy(req, *a, **kw):
        seen.append(req)
        return inner(req, *a, **kw)
    qe._slicer.slice.side_effect = spy
    return seen


@pytest.mark.asyncio
async def test_a_printers_preferred_provider_does_the_slice_and_keys_the_cache(session_factory, tmp_path, env):
    qe, _ = _engine(session_factory, tmp_path)
    seen = _capture(qe)
    job_id = await _seed(session_factory, save=True)
    await _prefer(session_factory, "b")

    providers = {"a": FakeSlicingProvider(), "b": FakeSlicingProvider()}
    with slicing_providers(providers, default="a"), \
            patch("app.services.slice_cache.current_fingerprint", return_value=FINGERPRINT) as fingerprint:
        await _run_to_printing(qe, session_factory, job_id)

    assert [r.provider_id for r in seen] == ["b"]                       # the slice runs on b, not the default a
    # The saved version records the fingerprint of the provider that sliced it, not of the default.
    assert fingerprint.call_args_list and all(c.args[3] is providers["b"] for c in fingerprint.call_args_list)
    async with session_factory() as s:
        from app.models import SlicedVersion
        (v,) = (await s.execute(select(SlicedVersion))).scalars().all()
    assert v.cache_key == slice_cache.cache_key(slice_cache.CacheKeyInputs(**{**INPUTS.__dict__, "provider": "b"}))


@pytest.mark.asyncio
async def test_without_a_preference_the_default_provider_is_used(session_factory, tmp_path, env):
    qe, _ = _engine(session_factory, tmp_path)
    seen = _capture(qe)
    job_id = await _seed(session_factory, save=False)

    with slicing_providers({"a": FakeSlicingProvider(), "b": FakeSlicingProvider()}, default="a"):
        await _run_to_printing(qe, session_factory, job_id)

    assert [r.provider_id for r in seen] == ["a"]


@pytest.mark.asyncio
async def test_a_down_preferred_provider_blocks_the_job_and_never_falls_back_to_the_default(session_factory, tmp_path, env):
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed(session_factory, save=False)
    await _prefer(session_factory, "b")

    with slicing_providers({"a": FakeSlicingProvider(), "b": down()}, default="a"):
        await qe._process_queue()
        await settle_background_tasks()

    qe._slicer.slice.assert_not_called()
    job = await _job(session_factory, job_id)
    assert (job.status, job.block_reason, job.assigned_printer_id) == ("blocked", "b is unreachable — slicing paused", None)


@pytest.mark.asyncio
async def test_a_dormant_preference_blocks_with_a_message_naming_the_printer(session_factory, tmp_path, env):
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed(session_factory, save=False)
    await _prefer(session_factory, "gone")

    with slicing_providers({"a": FakeSlicingProvider()}, default="a"):
        await qe._process_queue()
        await settle_background_tasks()

    qe._slicer.slice.assert_not_called()
    job = await _job(session_factory, job_id)
    assert job.status == "blocked" and "P1 is set to slice with" in job.block_reason and "disabled or removed" in job.block_reason


@pytest.mark.asyncio
async def test_a_dormant_preference_is_not_rescued_by_a_cached_version(session_factory, tmp_path, env, caplog):
    """The cache-only claim is for a provider that is DOWN; a missing one is the user's to fix."""
    caplog.set_level(logging.INFO, logger="app.services.slice_cache")
    # A version exists, keyed to the very provider the printer names: only the hard-block rule keeps it from being printed.
    await _add_version(session_factory, env, inputs=slice_cache.CacheKeyInputs(**{**INPUTS.__dict__, "provider": "gone"}))
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)
    await _prefer(session_factory, "gone")

    with slicing_providers({"a": FakeSlicingProvider()}, default="a"):
        await qe._process_queue()
        await settle_background_tasks()

    job = await _job(session_factory, job_id)
    assert job.status == "blocked" and job.sliced_version_id is None
    assert _cache_lines(caplog, "gate_bypass") == []


@pytest.mark.asyncio
async def test_a_down_preferred_provider_prints_a_cached_version_keyed_to_that_provider(session_factory, tmp_path, env, caplog):
    caplog.set_level(logging.INFO, logger="app.services.slice_cache")
    inputs_b = slice_cache.CacheKeyInputs(**{**INPUTS.__dict__, "provider": "b"})
    version_b = await _add_version(session_factory, env, inputs=inputs_b)
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)
    await _prefer(session_factory, "b")

    with slicing_providers({"a": FakeSlicingProvider(), "b": down()}, default="a"):
        await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_not_called()
    assert (await _job(session_factory, job_id)).sliced_version_id == version_b
    (line,) = _cache_lines(caplog, "gate_bypass")
    assert f"cache_key={slice_cache.cache_key(inputs_b)}" in line.getMessage()


@pytest.mark.asyncio
async def test_another_providers_cached_slice_never_serves_this_printer(session_factory, tmp_path, env):
    """A version saved by provider a (default key) must not be printed for a printer that prefers b, even with b down."""
    await _add_version(session_factory, env)                    # INPUTS carries provider "laminus"
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)
    await _prefer(session_factory, "b")

    with slicing_providers({"laminus": FakeSlicingProvider(), "b": down()}, default="laminus"):
        await qe._process_queue()
        await settle_background_tasks()

    job = await _job(session_factory, job_id)
    assert (job.status, job.sliced_version_id) == ("blocked", None)


@pytest.mark.asyncio
async def test_clearing_a_broken_preference_lets_the_default_take_the_blocked_job(session_factory, tmp_path, env):
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed(session_factory, save=False)
    await _prefer(session_factory, "b")

    with slicing_providers({"a": FakeSlicingProvider(), "b": down()}, default="a"):
        await qe._process_queue()
        await settle_background_tasks()
        assert (await _job(session_factory, job_id)).status == "blocked"
        await _prefer(session_factory, None)                    # clear the preference: the default takes it next cycle
        await _run_to_printing(qe, session_factory, job_id)
