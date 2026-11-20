import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import LibraryCatalogSchedule
from app.plugins.capabilities.library_source import CatalogPage, SourceFile
from app.services import library_sync as module
from app.services.library_catalog import _lease


class Host:
    def __init__(self):
        self.settings_value = {}
        self.active = True
        self.reads = 0
        self.error = None
        self.states = {}

    def settings(self, provider):
        return self.settings_value

    def active_for(self, capability, provider):
        return self if self.active else None

    def redact(self, provider, error):
        return error.replace("private-key", "***")

    async def record_state(self, provider, **values):
        self.states.update(values)

    async def call_for(self, handle, provider, fn, **kwargs):
        self.reads += 1
        if self.error:
            return SimpleNamespace(ok=False, error=self.error)
        return SimpleNamespace(
            ok=True,
            value=CatalogPage(
                (SourceFile("one", "part.stl", "https://source.test/one"),),
                full_scan=True,
            ),
        )


async def test_due_claim_survives_another_worker_and_successful_manual_sync_defers_it(
    session_factory, monkeypatch
):
    clock = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    first, second = Host(), Host()
    assert await module.sync_due(session_factory, first, "source")
    assert not await module.sync_due(session_factory, second, "source")
    assert first.reads == 1 and second.reads == 0
    clock[0] += 3601
    await module.synchronize(session_factory, second, "source")
    assert not await module.sync_due(session_factory, first, "source")
    assert second.reads == 1
    assert first.states["catalog_error"] is None and first.states["catalog_synced_at"]


async def test_failed_sync_is_redacted_and_backed_off_durably(session_factory):
    host = Host()
    host.error = "Source rejected private-key"
    assert await module.sync_due(session_factory, host, "source")
    assert host.states["catalog_error"] == "Source rejected ***"
    other = Host()
    assert not await module.sync_due(session_factory, other, "source")
    assert other.reads == 0


async def test_disabled_and_manual_only_sources_are_never_called(session_factory):
    host = Host()
    host.active = False
    assert not await module.sync_due(session_factory, host, "source")
    host.active = True
    host.settings_value = {"sync_interval_minutes": 0}
    assert not await module.sync_due(session_factory, host, "source")
    assert host.reads == 0
    async with session_factory() as session:
        assert (
            list((await session.execute(select(LibraryCatalogSchedule))).scalars())
            == []
        )


async def test_busy_source_retries_next_poll_without_overwriting_health(
    session_factory, monkeypatch
):
    clock = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    host = Host()
    async with _lease(session_factory, "source", "materializing"):
        assert not await module.sync_due(session_factory, host, "source")
    assert host.states == {} and host.reads == 0
    clock[0] += module.POLL_SECONDS
    assert await module.sync_due(session_factory, host, "source")


async def test_loop_survives_one_provider_failure_and_stops_its_task(monkeypatch):
    entered = asyncio.Event()
    calls = []
    monkeypatch.setattr(
        module,
        "providers_of",
        lambda capability: [
            SimpleNamespace(id="broken"),
            SimpleNamespace(id="healthy"),
        ],
    )

    async def due(factory, host, provider):
        calls.append(provider)
        if provider == "broken":
            raise ValueError("storage error")
        entered.set()

    monkeypatch.setattr(module, "sync_due", due)
    loop = module.LibrarySyncLoop()
    await loop.start(None, None)
    task = loop._task
    await loop.start(None, None)
    assert loop._task is task
    await asyncio.wait_for(entered.wait(), 2)
    await loop.stop()
    assert task.done() and loop._task is None and calls == ["broken", "healthy"]
    await loop.stop()


@pytest.mark.parametrize("value", [-1, True, "bad", 10081, None])
def test_invalid_untyped_provider_intervals_fall_back_to_default(value):
    host = Host()
    host.settings_value = {"sync_interval_minutes": value}
    assert module.interval_seconds(host, "source") == 3600


async def test_slow_source_does_not_block_another_and_shutdown_reaps_it(monkeypatch):
    slow_entered, healthy_done, closed = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    monkeypatch.setattr(
        module,
        "providers_of",
        lambda capability: [SimpleNamespace(id="slow"), SimpleNamespace(id="healthy")],
    )

    async def due(factory, host, provider):
        if provider == "slow":
            slow_entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        else:
            healthy_done.set()

    monkeypatch.setattr(module, "sync_due", due)
    loop = module.LibrarySyncLoop()
    await loop.start(None, None)
    await asyncio.wait_for(asyncio.gather(slow_entered.wait(), healthy_done.wait()), 2)
    await loop.stop()
    assert closed.is_set()


async def test_simultaneous_workers_make_one_durable_claim(session_factory):
    first, second = Host(), Host()
    results = await asyncio.gather(
        module.sync_due(session_factory, first, "source"),
        module.sync_due(session_factory, second, "source"),
    )
    assert sorted(results) == [False, True]
    assert first.reads + second.reads == 1
