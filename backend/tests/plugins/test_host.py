import asyncio

import pytest
from sqlalchemy import select

from app import plugins
from app.models import ExtensionSlot, PluginConfig
from app.plugins import PluginError
from tests.plugins.dummy_plugin import DummyProvider, make_manifest

KIND = "dummy_kind"


@pytest.fixture(autouse=True)
def _register():
    plugins.register_plugin(make_manifest("dummy_one"))
    plugins.register_plugin(make_manifest("dummy_two", capabilities=frozenset()))


async def _state(session_factory, plugin_id="dummy_one") -> dict:
    async with session_factory() as s:
        return dict((await s.get(PluginConfig, plugin_id)).state)


# --- the active rule --------------------------------------------------------------------------------------------

async def test_nothing_is_active_until_a_provider_is_selected(host):
    await host.start()
    assert host.active(KIND) is None and not host.has(KIND, "PING")


async def test_selecting_a_provider_enables_it_and_makes_it_active(host, session_factory):
    await host.start()
    await host.set_slot(KIND, "dummy_one")

    assert host.active(KIND).manifest.id == "dummy_one"
    assert host.has(KIND, "PING") and not host.has(KIND, "OTHER")
    async with session_factory() as s:
        assert (await s.get(PluginConfig, "dummy_one")).enabled is True
        assert (await s.get(ExtensionSlot, KIND)).plugin_id == "dummy_one"


async def test_disabling_keeps_the_selection_but_makes_it_inactive_and_enabling_restores_it(host, session_factory):
    await host.start()
    await host.set_slot(KIND, "dummy_one")

    await host.update_config("dummy_one", enabled=False)
    assert host.active(KIND) is None
    async with session_factory() as s:
        assert (await s.get(ExtensionSlot, KIND)).plugin_id == "dummy_one"      # the choice survives

    await host.update_config("dummy_one", enabled=True)
    assert host.active(KIND).manifest.id == "dummy_one"


async def test_an_enabled_plugin_that_is_not_in_the_slot_is_not_active(host):
    await host.start()
    await host.set_slot(KIND, "dummy_one")
    await host.update_config("dummy_two", enabled=True)
    assert host.active(KIND).manifest.id == "dummy_one"
    await host.set_slot(KIND, None)
    assert host.active(KIND) is None


async def test_the_selection_is_restored_from_the_db_by_a_new_host(host, session_factory):
    await host.start()
    await host.set_slot(KIND, "dummy_two")
    from app.plugins.host import PluginHost
    fresh = PluginHost()
    fresh.configure(session_factory)
    await fresh.start()
    assert fresh.active(KIND).manifest.id == "dummy_two"


async def test_a_slot_can_only_hold_a_plugin_of_its_own_kind(host):
    await host.start()
    with pytest.raises(PluginError):
        await host.set_slot("some_other_kind", "dummy_one")
    with pytest.raises(PluginError):
        await host.set_slot(KIND, "not_registered")


# --- configuration ---------------------------------------------------------------------------------------------

async def test_a_settings_change_rebuilds_the_instance_in_place_without_a_restart(host):
    await host.set_slot(KIND, "dummy_one")
    first = host.active(KIND).instance

    await host.update_config("dummy_one", settings={"url": "http://elsewhere.test"})

    second = host.active(KIND).instance
    assert second is not first and second.settings.url == "http://elsewhere.test"
    assert first.closed is True                                  # the old instance was closed


async def test_secrets_are_write_only_omit_keeps_empty_clears_and_non_secrets_are_refused(host, session_factory):
    await host.set_slot(KIND, "dummy_one")
    await host.update_config("dummy_one", secrets={"token": "s3cret"})
    assert host.active(KIND).instance.settings.token == "s3cret"

    await host.update_config("dummy_one", settings={"url": "http://x.test"})          # omitted -> kept
    assert host.active(KIND).instance.settings.token == "s3cret"
    async with session_factory() as s:
        row = await s.get(PluginConfig, "dummy_one")
        assert row.secrets == {"token": "s3cret"} and "token" not in row.settings          # never stored as a setting

    await host.update_config("dummy_one", secrets={"token": ""})                       # cleared
    assert host.active(KIND).instance.settings.token is None

    with pytest.raises(PluginError):
        await host.update_config("dummy_one", secrets={"url": "x"})


async def test_an_invalid_settings_change_is_rejected_and_changes_nothing(host, session_factory):
    await host.set_slot(KIND, "dummy_one")
    with pytest.raises(PluginError, match="url"):
        await host.update_config("dummy_one", settings={"url": ["not", "a", "string"]})
    async with session_factory() as s:
        assert (await s.get(PluginConfig, "dummy_one")).settings == {}
    assert host.active(KIND) is not None


async def test_configuring_an_unknown_plugin_is_an_error(host):
    with pytest.raises(PluginError):
        await host.update_config("nope_nope", enabled=True)


# --- failure containment (these fail if a caller bypasses host.call) ---------------------------------------------

async def test_a_call_returns_the_value_and_records_health(host, session_factory):
    await host.set_slot(KIND, "dummy_one")
    r = await host.call(KIND, "ping", "hello")
    assert (r.ok, r.value, r.reason) == (True, "hello", "ok")
    assert (await _state(session_factory))["last_ok_at"]


async def test_a_provider_that_raises_never_raises_into_the_caller_and_last_error_is_written(host, session_factory):
    await host.set_slot(KIND, "dummy_one")
    await host.update_config("dummy_one", settings={"mode": "raise"})

    with pytest.raises(RuntimeError):                         # sanity: bypassing the wrapper DOES blow up
        await host.active(KIND).instance.ping()
    r = await host.call(KIND, "ping")

    assert (r.ok, r.reason) == (False, "error") and "provider exploded" in r.error
    assert "provider exploded" in (await _state(session_factory))["last_error"]


async def test_a_provider_that_hangs_times_out_and_last_error_is_written(host, session_factory):
    await host.set_slot(KIND, "dummy_one")
    await host.update_config("dummy_one", settings={"mode": "hang"})

    r = await asyncio.wait_for(host.call(KIND, "ping", timeout=0.05), timeout=5)

    assert (r.ok, r.reason) == (False, "timeout")
    assert "timed out" in (await _state(session_factory))["last_error"]


async def test_the_error_clears_when_the_provider_recovers(host, session_factory):
    await host.set_slot(KIND, "dummy_one")
    await host.update_config("dummy_one", settings={"mode": "raise"})
    await host.call(KIND, "ping")
    assert (await _state(session_factory))["last_error"]

    await host.update_config("dummy_one", settings={"mode": "ok"})
    assert (await host.call(KIND, "ping")).ok
    assert (await _state(session_factory))["last_error"] is None


async def test_calling_with_no_active_provider_or_an_unknown_method_is_a_typed_failure(host):
    assert (await host.call(KIND, "ping")).reason == "inactive"
    await host.set_slot(KIND, "dummy_one")
    r = await host.call(KIND, "does_not_exist")
    assert (r.ok, r.reason) == (False, "inactive")


async def test_a_provider_that_cannot_be_built_is_contained_and_reported_and_the_host_still_starts(host, session_factory):
    await host.set_slot(KIND, "dummy_one")
    await host.update_config("dummy_one", settings={"mode": "bad-config"})      # reload() must not raise

    assert host.active(KIND) is None
    assert "cannot build" in host.build_error("dummy_one")
    assert "cannot build" in (await _state(session_factory))["last_error"]
    # fixing the config brings it back, in place
    await host.update_config("dummy_one", settings={"mode": "ok"})
    assert host.active(KIND) is not None and host.build_error("dummy_one") is None


async def test_stop_closes_every_instance(host):
    await host.set_slot(KIND, "dummy_one")
    inst = host.active(KIND).instance
    await host.stop()
    assert inst.closed is True and host.active(KIND) is None


async def test_a_healthy_poll_does_not_rewrite_the_state_row_every_time(host, session_factory, monkeypatch):
    from app.plugins import host as host_module
    await host.set_slot(KIND, "dummy_one")
    monkeypatch.setattr(host_module, "_now", lambda: "2026-01-01T00:00:00")
    await host.call(KIND, "ping")
    monkeypatch.setattr(host_module, "_now", lambda: "2026-01-01T00:05:00")
    await host.call(KIND, "ping")                             # still healthy: nothing to persist
    assert (await _state(session_factory))["last_ok_at"] == "2026-01-01T00:00:00"


# --- review hardening (BIZ-205) ---------------------------------------------------------------------------------

async def test_settings_may_not_carry_a_secret_field(host):
    await host.set_slot(KIND, "dummy_one")
    with pytest.raises(PluginError, match="secret"):
        await host.update_config("dummy_one", settings={"token": "plaintext"})


async def test_a_secret_never_reaches_persisted_state_or_the_error_a_caller_sees(host, session_factory):
    from tests.plugins.dummy_plugin import DummyProvider
    await host.set_slot(KIND, "dummy_one")
    await host.update_config("dummy_one", secrets={"token": "hunter2-token"})

    async def leaky(self, value="x"):
        raise RuntimeError("401 for https://x.test/?key=hunter2-token")
    DummyProvider.ping, original = leaky, DummyProvider.ping
    try:
        r = await host.call(KIND, "ping")
    finally:
        DummyProvider.ping = original
    assert "hunter2-token" not in r.error and "***" in r.error
    assert "hunter2-token" not in str(await _state(session_factory))


async def test_a_validation_error_does_not_echo_the_offending_value(host):
    await host.set_slot(KIND, "dummy_one")
    with pytest.raises(PluginError) as e:
        await host.update_config("dummy_one", settings={"url": {"password": "topsecret-value"}})
    assert "topsecret-value" not in str(e.value)


async def test_overlapping_config_changes_leave_exactly_one_live_instance_and_close_the_rest(host):
    await host.set_slot(KIND, "dummy_one")
    await asyncio.gather(*(host.update_config("dummy_one", settings={"url": f"http://u{i}.test"}) for i in range(5)))

    live = [i for i in DummyProvider.instances if not i.closed]
    assert live == [host.active(KIND).instance]                   # nothing leaked, nothing active is closed
    async with host._session_factory() as s:                      # and the instance reflects the config that won
        stored = (await s.get(PluginConfig, "dummy_one")).settings["url"]
    assert host.active(KIND).instance.settings.url == stored


async def test_changing_one_plugin_does_not_rebuild_another(host):
    plugins.register_plugin(make_manifest("other_kind_plugin", kind="other_kind"))
    await host.set_slot(KIND, "dummy_one")
    await host.set_slot("other_kind", "other_kind_plugin")
    other = host.active("other_kind").instance

    await host.update_config("dummy_one", settings={"url": "http://changed.test"})

    assert host.active("other_kind").instance is other and not other.closed


async def test_a_reload_without_changes_keeps_the_instances(host):
    await host.set_slot(KIND, "dummy_one")
    inst = host.active(KIND).instance
    await host.reload()
    assert host.active(KIND).instance is inst


async def test_a_call_that_straddles_a_reload_does_not_blame_the_new_instance(host, session_factory):
    await host.set_slot(KIND, "dummy_one")
    await host.update_config("dummy_one", settings={"mode": "hang"})
    pending = asyncio.create_task(host.call(KIND, "ping", timeout=0.2))
    await asyncio.sleep(0)                                         # the call is now awaiting the old instance
    await host.update_config("dummy_one", settings={"mode": "ok"})
    r = await pending

    assert r.reason == "timeout"
    assert (await _state(session_factory)).get("last_error") is None   # the replaced instance's failure was not recorded


async def test_a_successful_rebuild_clears_a_build_error_from_the_state(host, session_factory):
    await host.set_slot(KIND, "dummy_one")
    await host.update_config("dummy_one", settings={"mode": "bad-config"})
    assert (await _state(session_factory))["last_error"]
    await host.update_config("dummy_one", settings={"mode": "ok"})
    assert (await _state(session_factory))["last_error"] is None


async def test_a_failed_state_write_leaves_memory_unchanged_so_the_next_change_retries(host, session_factory):
    await host.set_slot(KIND, "dummy_one")
    good_factory = host._session_factory

    def broken():
        raise RuntimeError("db down")
    host._session_factory = broken
    await host._record("dummy_one", last_error="boom")
    assert host.state("dummy_one").get("last_error") is None       # not remembered as persisted

    host._session_factory = good_factory
    await host._record("dummy_one", last_error="boom")
    assert (await _state(session_factory))["last_error"] == "boom"


async def test_a_plugin_whose_migration_failed_is_never_built(host, session_factory):
    from app.plugins import migrations as plugin_migrations
    await host.set_slot(KIND, "dummy_one")
    plugin_migrations.failed["dummy_one"] = "migration v1 (m) failed: no such table"
    try:
        await host.reload()
        assert host.active(KIND) is None and "migration v1" in host.build_error("dummy_one")
    finally:
        plugin_migrations.failed.clear()
    await host.reload()
    assert host.active(KIND) is not None


async def test_a_plugin_raising_cancellation_internally_is_a_failure_not_a_cancel_of_the_caller(host):
    from tests.plugins.dummy_plugin import DummyProvider

    async def cancels(self, value="x"):
        raise asyncio.CancelledError()
    DummyProvider.ping, original = cancels, DummyProvider.ping
    await host.set_slot(KIND, "dummy_one")
    try:
        r = await host.call(KIND, "ping")
    finally:
        DummyProvider.ping = original
    assert (r.ok, r.reason) == (False, "error")


async def test_reloads_never_run_their_rebuild_phases_concurrently(host, monkeypatch):
    """Serialisation, observed directly: instance closes (which await) from overlapping reloads must not interleave."""
    running, peak = 0, 0

    async def slow_close(self):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        self.closed = True
    monkeypatch.setattr(DummyProvider, "aclose", slow_close)
    await host.set_slot(KIND, "dummy_one")

    await asyncio.gather(*(host.update_config("dummy_one", settings={"url": f"http://n{i}.test"}) for i in range(4)))

    assert peak == 1
