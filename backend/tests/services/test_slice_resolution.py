"""BIZ-291: which slicing provider takes a job on a printer, and is it usable (resolve_slicing over fake producers)."""
from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import slice_resolution as sr
from app.services.providers import contract as c
from app.services.providers.contract import Action, Reason, Readiness
from tests.fake_providers import FakeGcodeProducer

pytestmark = pytest.mark.asyncio


def printer(preferred=None, **kw):
    return SimpleNamespace(name="P1", preferred_slicer_plugin_id=preferred, bed_x_mm=256.0, bed_y_mm=256.0,
                           model_uuid=kw.pop("model_uuid", "uuid-x1c"), **kw)


@pytest.fixture
def world():
    """`world(enabled=..., default=..., producers=...)` installs fake providers; producers absent from `producers` read as unconfigured.
    Uses `patch` (not `monkeypatch`): the autouse slicing fixture of this package patches the same names, and monkeypatch can be torn
    down after it, which would restore ITS stale values and leak them into later tests."""
    with contextlib.ExitStack() as stack:
        def install(enabled, default, producers):
            for name, value in (("enabled_provider_ids", lambda: list(enabled)), ("default_provider_id", lambda: default),
                                ("gcode_producer_for", lambda pid: producers.get(pid)),
                                ("get_plugin", lambda pid: SimpleNamespace(name=pid.title()))):
                stack.enter_context(patch.object(sr, name, value))
        yield install


async def test_default_provider_ready_produces(world):
    a = FakeGcodeProducer("a")
    world(["a"], "a", {"a": a})
    d = await sr.resolve_slicing(printer(), "m.3mf")
    assert not d.blocked and d.provider_id == "a" and d.outcome.action is Action.PRODUCE and d.message is None


async def test_stl_is_a_model_input_too(world):
    a = FakeGcodeProducer("a")
    world(["a"], "a", {"a": a})
    assert (await sr.resolve_slicing(printer(), "part.STL")).outcome.action is Action.PRODUCE
    a2 = FakeGcodeProducer("a", accepted=[c.InputKind.MODEL_3MF])      # a provider that takes only 3MF
    world(["a"], "a", {"a": a2})
    d = await sr.resolve_slicing(printer(), "part.stl")
    assert d.outcome.reason is Reason.DEFAULT_INELIGIBLE and "can't slice for P1" in d.message and not d.recoverable


async def test_preference_wins_over_the_default(world):
    a, b = FakeGcodeProducer("a"), FakeGcodeProducer("b")
    world(["a", "b"], "a", {"a": a, "b": b})
    d = await sr.resolve_slicing(printer(preferred="b"), "m.3mf")
    assert d.provider_id == "b" and d.outcome.action is Action.PRODUCE


async def test_only_the_chosen_provider_is_probed(world):
    class Counting(FakeGcodeProducer):
        probes = 0

        def status(self, timeout=2.0):
            type(self).probes += 1
            return super().status(timeout)
    a, b = Counting("a"), Counting("b")
    world(["a", "b"], "a", {"a": a, "b": b})
    await sr.resolve_slicing(printer(preferred="b"), "m.3mf")
    assert Counting.probes == 1


@pytest.mark.parametrize("state,text", [
    (Readiness.NOT_READY, "A is not ready — slicing paused"),
    (Readiness.UNREACHABLE, "A is unreachable — slicing paused"),
])
async def test_default_down_blocks_recoverably_with_the_state_text(world, state, text):
    world(["a"], "a", {"a": FakeGcodeProducer("a", state=state)})
    d = await sr.resolve_slicing(printer(), "m.3mf")
    assert d.blocked and d.recoverable and d.message == text
    assert d.outcome.reason is Reason.DEFAULT_UNAVAILABLE and d.provider_id == "a"   # the id survives, for the cache lookup


async def test_unconfigured_default_reads_as_down_not_missing(world):
    world(["a"], "a", {})                                   # enabled, but gcode_producer_for() -> None (no URL)
    d = await sr.resolve_slicing(printer(), "m.3mf")
    assert d.blocked and d.recoverable and d.message == "A sidecar not configured — slicing paused" and d.provider_id == "a"


async def test_preferred_provider_down_never_falls_back_to_the_default(world):
    a, b = FakeGcodeProducer("a"), FakeGcodeProducer("b", state=Readiness.UNREACHABLE)
    world(["a", "b"], "a", {"a": a, "b": b})
    d = await sr.resolve_slicing(printer(preferred="b"), "m.3mf")
    assert d.blocked and d.recoverable and d.outcome.reason is Reason.PREFERENCE_UNAVAILABLE and d.provider_id == "b"
    assert d.message == "B is unreachable — slicing paused"


async def test_unconfigured_preferred_provider_blocks_with_the_preference_reason(world):
    world(["a", "b"], "a", {"a": FakeGcodeProducer("a")})
    d = await sr.resolve_slicing(printer(preferred="b"), "m.3mf")
    assert d.blocked and d.recoverable and d.outcome.reason is Reason.PREFERENCE_UNAVAILABLE and "B sidecar not configured" in d.message


async def test_dormant_preference_is_a_hard_block_naming_the_printer(world):
    world(["a"], "a", {"a": FakeGcodeProducer("a")})
    d = await sr.resolve_slicing(printer(preferred="gone"), "m.3mf")
    assert d.blocked and not d.recoverable and d.outcome.reason is Reason.PREFERENCE_DORMANT
    assert d.message == "P1 is set to slice with Gone, which is disabled or removed — slicing paused"


async def test_ineligible_preference_blocks_even_when_the_default_could_take_it(world):
    a = FakeGcodeProducer("a")
    b = FakeGcodeProducer("b", models=frozenset({"someone-else"}))
    world(["a", "b"], "a", {"a": a, "b": b})
    d = await sr.resolve_slicing(printer(preferred="b"), "m.3mf")
    assert d.outcome.reason is Reason.PREFERENCE_INELIGIBLE and not d.recoverable and d.message == "B can't slice for P1 — slicing paused"


async def test_no_provider_enabled(world):
    world([], None, {})
    d = await sr.resolve_slicing(printer(), "m.3mf")
    assert d.blocked and not d.recoverable and d.outcome.reason is Reason.NO_PROVIDER
    assert d.message == "No slicing provider is enabled — slicing paused"


async def test_several_enabled_and_no_default_is_a_hard_block(world):
    world(["a", "b"], None, {"a": FakeGcodeProducer("a"), "b": FakeGcodeProducer("b")})
    d = await sr.resolve_slicing(printer(), "m.3mf")
    assert d.blocked and not d.recoverable and "none is the default" in d.message
    assert (await sr.resolve_slicing(printer(preferred="b"), "m.3mf")).outcome.action is Action.PRODUCE   # a preference needs no default


async def test_target_comes_from_the_printer_row(world):
    a = FakeGcodeProducer("a", models=frozenset({"uuid-u1"}))
    world(["a"], "a", {"a": a})
    assert (await sr.resolve_slicing(printer(model_uuid="uuid-u1"), "m.3mf")).outcome.action is Action.PRODUCE
    assert (await sr.resolve_slicing(printer(model_uuid="uuid-x1c"), "m.3mf")).blocked
    t = sr.target_of(printer())
    assert t.bed_mm[:2] == (256.0, 256.0) and t.model_uuid == "uuid-x1c"
