import asyncio

import pytest
from pydantic import ValidationError
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
    with pytest.raises(ValidationError):
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
