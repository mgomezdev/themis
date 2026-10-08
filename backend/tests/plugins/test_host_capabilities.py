import pytest

from app import plugins
from app.plugins import PluginError
from app.plugins.capabilities.definition import CapabilityDef
from app.plugins.manifest import Provide, Requirement
from tests.plugins.dummy_plugin import DummyProvider, make_manifest


def reg(*ms):
    for m in ms:
        plugins.register_plugin(m)


async def enable(host, *ids):
    for i in ids:
        await host.update_config(i, enabled=True)


def definer(cap="shared.ping", methods=("ping",)):
    """A plugin that only *defines* `cap` (provides nothing), so several others can provide it."""
    return make_manifest("shared", provides={}, defines=(CapabilityDef(cap, 1, "Shared", required_methods=methods),))


def provider(pid, cap="shared.ping", **over):
    return make_manifest(pid, provides={cap: Provide(features=frozenset({"PING"}))}, defines=(), **over)


async def test_auto_select_only_the_unambiguous_case(host):
    reg(definer(), provider("plug_a"), provider("plug_b"))
    await host.start()
    await enable(host, "plug_a")
    assert host.selected("shared.ping") == "plug_a" and not host.is_explicit("shared.ping")
    await enable(host, "plug_b")                                  # a later provider never displaces a choice
    assert host.selected("shared.ping") == "plug_a" and host.active("shared.ping").manifest.id == "plug_a"


async def test_two_enabled_providers_and_no_row_selects_nothing(host):
    reg(definer(), provider("plug_a"), provider("plug_b"))
    await host.start()
    await host.update_config("plug_a", enabled=True)              # auto-selected while alone
    await host.set_provider("shared.ping", None)                  # user clears it
    await enable(host, "plug_b")                                  # a sole *other* enabled provider must not be auto-selected now
    assert host.selected("shared.ping") is None and host.is_explicit("shared.ping")
    assert host.active("shared.ping") is None


async def test_explicit_none_is_remembered_across_reload(host):
    reg(definer(), provider("plug_a"))
    await host.start()
    await host.set_provider("shared.ping", None)
    await host.reload()
    await enable(host, "plug_a")
    assert host.selected("shared.ping") is None and host.status("shared.ping").state == "none_selected"


async def test_set_provider_enables_the_plugin_and_builds_one_instance_for_two_capabilities(host):
    m = make_manifest("multi", cap="multi.one", provides={"multi.one": Provide(), "multi.two": Provide()},
                      defines=(CapabilityDef("multi.one", 1, "One"), CapabilityDef("multi.two", 1, "Two")))
    reg(m)
    await host.start()
    await host.set_provider("multi.one", "multi")
    await host.set_provider("multi.two", "multi")
    assert host.is_enabled("multi") and len(DummyProvider.instances) == 1
    assert host.active("multi.one").instance is host.active("multi.two").instance


async def test_part_uses_attr_and_features_and_call_goes_to_the_part(host):
    class Svc:
        async def ping(self, value="pong"):
            return f"svc:{value}"

    class Provider(DummyProvider):
        def __init__(self, settings):
            super().__init__(settings)
            self.svc = Svc()

    m = make_manifest("attrp", cap="attrp.ping", factory=Provider,
                      provides={"attrp.ping": Provide(attr="svc", features=frozenset({"X"}))})
    reg(m)
    await host.start()
    await host.set_provider("attrp.ping", "attrp")
    assert host.has("attrp.ping", "X") and not host.has("attrp.ping", "Y")
    assert isinstance(host.part("attrp.ping"), Svc)
    r = await host.call("attrp.ping", "ping", "hi")
    assert r.ok and r.value == "svc:hi"


async def test_unmet_requires_makes_a_plugin_wait_and_offer_nothing(host):
    base = make_manifest("plug_a", cap="plug_a.ping")
    consumer = make_manifest("consumer", cap="consumer.use", requires=(Requirement("plug_a.ping"),))
    reg(base, consumer)
    await host.start()
    await host.set_provider("consumer.use", "consumer")
    st = host.status("consumer.use")
    assert (st.state, st.waiting_on) == ("waiting", ("plug_a.ping",))
    assert host.active("consumer.use") is None and DummyProvider.instances == []
    await host.set_provider("plug_a.ping", "plug_a")              # no restart needed
    assert host.status("consumer.use").state == "serving" and len(DummyProvider.instances) == 2


async def test_requires_min_version(host):
    base = make_manifest("plug_a", cap="plug_a.ping")             # provides v1
    consumer = make_manifest("consumer", cap="consumer.use", requires=(Requirement("plug_a.ping", 2),))
    reg(base, consumer)
    await host.start()
    await host.set_provider("plug_a.ping", "plug_a")
    await host.set_provider("consumer.use", "consumer")
    assert host.status("consumer.use").state == "waiting"


async def test_selection_cycle_is_rejected_and_leaves_no_row(host, session_factory):
    a = make_manifest("plug_a", cap="plug_a.x", requires=(Requirement("plug_b.x"),))
    b = make_manifest("plug_b", cap="plug_b.x", requires=(Requirement("plug_a.x"),))
    reg(a, b)
    await host.start()
    await host.set_provider("plug_b.x", "plug_b")                 # fine on its own: b is waiting on a.x
    with pytest.raises(PluginError, match="cycle"):
        await host.set_provider("plug_a.x", "plug_a")
    assert host.selected("plug_a.x") is None
    from app.models import CapabilitySelection
    async with session_factory() as s:
        assert await s.get(CapabilitySelection, "plug_a.x") is None


async def test_requirement_cycle_reached_by_auto_select_does_not_recurse_forever(host):
    a = make_manifest("plug_a", cap="plug_a.x", requires=(Requirement("plug_b.x"),))
    b = make_manifest("plug_b", cap="plug_b.x", requires=(Requirement("plug_a.x"),))
    reg(a, b)
    await host.start()
    await enable(host, "plug_a", "plug_b")                        # both auto-selected as sole providers
    assert host.status("plug_a.x").state == "waiting" and host.status("plug_b.x").state == "waiting"
    assert DummyProvider.instances == []


async def test_dormant_selection_when_the_definer_is_uninstalled_and_restored_when_it_returns(host):
    m = make_manifest("plug_a", cap="plug_a.ping")
    reg(m)
    await host.start()
    await host.set_provider("plug_a.ping", "plug_a")
    plugins._REGISTRY.pop("plug_a")
    await host.reload()
    assert host.status("plug_a.ping").state == "dormant" and host.active("plug_a.ping") is None
    assert host.selected("plug_a.ping") == "plug_a"               # kept
    reg(m)
    await host.reload()
    assert host.status("plug_a.ping").state == "serving"


async def test_selection_for_a_plugin_that_no_longer_exists_is_no_provider(host):
    reg(definer(), provider("plug_a"))
    await host.start()
    await host.set_provider("shared.ping", "plug_a")
    plugins._REGISTRY.pop("plug_a")
    await host.reload()
    assert host.status("shared.ping").state == "no_provider" and host.active("shared.ping") is None


async def test_duck_typing_required_methods_are_checked_when_the_instance_is_built(host):
    reg(definer(methods=("ping", "pong")), provider("plug_a"))     # DummyProvider has ping but not pong
    await host.start()
    await host.set_provider("shared.ping", "plug_a")
    st = host.status("shared.ping")
    assert st.state == "error" and "pong" in (st.error or "") and host.active("shared.ping") is None


async def test_provide_version_must_match_the_definition(host):
    reg(definer(), make_manifest("plug_a", provides={"shared.ping": Provide(version=2)}, defines=()))
    await host.start()
    await host.set_provider("shared.ping", "plug_a")
    assert host.status("shared.ping").state == "error" and "v2" in (host.status("shared.ping").error or "")


async def test_provide_attr_that_the_instance_lacks_is_a_build_error(host):
    reg(definer(), make_manifest("plug_a", provides={"shared.ping": Provide(attr="nope")}, defines=()))
    await host.start()
    await host.set_provider("shared.ping", "plug_a")
    assert host.status("shared.ping").state == "error" and "nope" in (host.status("shared.ping").error or "")


async def test_set_provider_rejects_unknown_capability_and_non_provider(host):
    reg(definer(), provider("plug_a"), make_manifest("other", cap="other.ping"))
    await host.start()
    with pytest.raises(PluginError, match="unknown capability"):
        await host.set_provider("nope.nothing", "plug_a")
    with pytest.raises(PluginError, match="does not provide"):
        await host.set_provider("shared.ping", "other")


async def test_call_is_contained_by_capability(host):
    reg(make_manifest("plug_a", cap="plug_a.ping"))
    await host.start()
    await host.update_config("plug_a", enabled=True, settings={"mode": "raise"})
    r = await host.call("plug_a.ping", "ping")
    assert (r.ok, r.reason) == (False, "error") and "exploded" in r.error
    assert (await host.call("nothing.here", "ping")).reason == "inactive"
    assert (await host.call("plug_a.ping", "no_such_method")).reason == "inactive"
    await host.update_config("plug_a", settings={"mode": "hang"})
    assert (await host.call("plug_a.ping", "ping", timeout=0.05)).reason == "timeout"


async def test_two_enabled_providers_with_no_stored_choice_are_not_auto_selected(host, session_factory):
    from app.models import PluginConfig
    reg(definer(), provider("plug_a"), provider("plug_b"))
    async with session_factory() as s:                            # both already enabled before the host ever looks (e.g. an upgrade)
        for pid in ("plug_a", "plug_b"):
            s.add(PluginConfig(plugin_id=pid, enabled=True, settings={}, secrets={}, state={}))
        await s.commit()
    await host.start()
    assert host.selected("shared.ping") is None and host.active("shared.ping") is None
    assert host.status("shared.ping").state == "no_provider" or host.status("shared.ping").state == "none_selected"


async def test_one_bad_provide_entry_does_not_take_down_the_plugins_other_capabilities(host):
    reg(definer(), make_manifest("multi", provides={"shared.ping": Provide(version=2), "multi.ok": Provide()},
                                 defines=(CapabilityDef("multi.ok", 1, "Fine"),)))
    await host.start()
    await host.set_provider("multi.ok", "multi")
    await host.set_provider("shared.ping", "multi")
    assert host.status("multi.ok").state == "serving" and host.active("multi.ok") is not None
    bad = host.status("shared.ping")
    assert bad.state == "error" and "v2" in (bad.error or "") and host.active("shared.ping") is None
    assert host.build_error("multi") is None                            # the plugin itself built fine
