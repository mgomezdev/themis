"""Fan-out of a notification to every enabled `notify.channel` plugin (BIZ-252): a channel plugin works with no core change, channels
are independent, and enabling/disabling/uninstalling is honoured."""
import asyncio
import logging

import httpx
import pytest
from pydantic import Field

from app import plugins
from app.plugins.capabilities.notify_channel import CAPABILITY, ChannelMessage, ChannelResult, ChannelSettings, FilteredChannel
from app.plugins.host import plugin_host
from app.plugins.manifest import HOST_API, PluginManifest, Provide
from app.services import notify as notify_mod
from app.services.notify import notify
from tests.waiting import wait_until


class FakeSettings(ChannelSettings):
    mode: str = "ok"                                         # ok | raise | hang | refuse
    api_token: str | None = Field(default=None)


class FakeChannel(FilteredChannel):
    """A third-party channel: it exists only in this test module, and core has never heard of it."""
    inboxes: dict[str, list[ChannelMessage]] = {}

    def __init__(self, settings: FakeSettings) -> None:
        self.settings = settings
        self.secrets = (settings.api_token or "",)
        self.inbox = FakeChannel.inboxes.setdefault(settings.mode + str(id(self)), [])
        FakeChannel.last = self

    def configured(self) -> bool:
        return True

    async def send(self, message: ChannelMessage) -> None:
        if self.settings.mode == "raise":
            raise RuntimeError(f"upstream said no to token {self.settings.api_token}")
        if self.settings.mode == "hang":
            await asyncio.sleep(60)
        self.inbox.append(message)


def manifest(plugin_id: str, **over) -> PluginManifest:
    fields = dict(id=plugin_id, name=plugin_id, version="1.0.0", host_api=HOST_API, settings_model=FakeSettings, factory=FakeChannel,
                  secret_fields=frozenset({"api_token"}), provides={CAPABILITY: Provide(version=1)})
    fields.update(over)
    return PluginManifest(**fields)


@pytest.fixture(autouse=True)
async def _host(session_factory):
    FakeChannel.inboxes = {}
    for pid in ("notify_ntfy", "notify_discord", "notify_email"):      # the bundled channels are out of the way for these tests
        plugins._REGISTRY.pop(pid, None)
    await plugin_host.start()
    notify_mod._seen.clear()
    yield


async def enable(pid: str, **settings) -> FakeChannel:
    token = settings.pop("api_token", None)
    await plugin_host.update_config(pid, enabled=True, settings=settings or {"events": ["job.complete"]},
                                    secrets={"api_token": token} if token else None)
    return plugin_host._instances[pid]


def inbox(channel: FakeChannel) -> list[ChannelMessage]:
    return channel.inbox


async def test_a_new_channel_plugin_receives_matching_events_with_no_core_change():
    plugins.register_plugin(manifest("fake_chat"))
    ch = await enable("fake_chat", events=["job.complete"])

    out = await notify("job.complete", 7, "Title", "Body", message_id="m-1")
    await notify("job.failed", 7, "Other", "Other")

    assert out == {"fake_chat": "ok"}
    assert [(m.event, m.title, m.message, m.job_id, m.message_id) for m in inbox(ch)] == [("job.complete", "Title", "Body", 7, "m-1")]


async def test_two_enabled_channels_receive_independently_and_a_failing_one_does_not_block_the_other():
    plugins.register_plugin(manifest("fake_good"))
    plugins.register_plugin(manifest("fake_bad"))
    good = await enable("fake_good", events=["job.complete"])
    await enable("fake_bad", events=["job.complete"], mode="raise", api_token="tok-SECRET")

    out = await notify("job.complete", 1, "T", "M")

    assert len(inbox(good)) == 1 and out["fake_good"] == "ok"
    assert out["fake_bad"].startswith("RuntimeError") and "tok-SECRET" not in out["fake_bad"]


async def test_a_hanging_channel_times_out_without_delaying_the_others(monkeypatch):
    monkeypatch.setattr(notify_mod, "CHANNEL_TIMEOUT_S", 0.1)
    plugins.register_plugin(manifest("fake_fast"))
    plugins.register_plugin(manifest("fake_slow"))
    fast = await enable("fake_fast", events=["job.complete"])
    await enable("fake_slow", events=["job.complete"], mode="hang")

    started = asyncio.get_running_loop().time()
    out = await notify("job.complete", 1, "T", "M")

    assert asyncio.get_running_loop().time() - started < 2 and len(inbox(fast)) == 1
    assert "timed out" in out["fake_slow"]


async def test_a_disabled_channel_receives_nothing_and_resumes_when_enabled_again():
    plugins.register_plugin(manifest("fake_toggle"))
    ch = await enable("fake_toggle", events=["job.complete"])
    await plugin_host.update_config("fake_toggle", enabled=False)

    assert await notify("job.complete", 1, "T", "M") == {} and inbox(ch) == []

    ch2 = await enable("fake_toggle", events=["job.complete"])
    await notify("job.complete", 2, "T", "M")
    assert [m.job_id for m in inbox(ch2)] == [2]


async def test_an_uninstalled_channel_receives_nothing():
    plugins.register_plugin(manifest("fake_gone"))
    await enable("fake_gone", events=["job.complete"])
    plugins._REGISTRY.pop("fake_gone")

    assert await notify("job.complete", 1, "T", "M") == {}


async def test_a_repeated_message_id_notifies_once():
    plugins.register_plugin(manifest("fake_once"))
    ch = await enable("fake_once", events=["job.complete"])

    await notify("job.complete", 1, "T", "M", message_id="evt-9")
    again = await notify("job.complete", 1, "T", "M", message_id="evt-9")                # a redelivered event
    await notify("job.complete", 1, "T", "M")                                            # no id: never deduplicated
    await notify("job.complete", 1, "T", "M")

    assert again == {} and len(inbox(ch)) == 3


async def test_failures_are_logged_without_secrets(caplog):
    plugins.register_plugin(manifest("fake_log"))
    await enable("fake_log", events=["job.complete"], mode="raise", api_token="tok-LOGGED-SECRET")

    with caplog.at_level(logging.WARNING, logger="app"):
        await notify("job.complete", 1, "T", "M")

    assert "fake_log" in caplog.text and "tok-LOGGED-SECRET" not in caplog.text


async def test_notify_never_raises_even_if_the_host_does(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("host exploded")

    monkeypatch.setattr(plugin_host, "fan_out", boom)
    assert await notify("job.complete", 1, "T", "M") == {}


async def test_an_unhealthy_channel_is_visible_as_the_plugins_error(client):
    plugins.register_plugin(manifest("fake_health"))
    await enable("fake_health", events=["job.complete"], mode="raise")
    await notify("job.complete", 1, "T", "M")

    body = (await client.get("/api/v1/plugins/fake_health")).json()
    assert body["state"]["last_error"] and "no" in body["state"]["last_error"]


async def test_the_generic_test_button_sends_a_test_message_or_reports_why_not(client):
    plugins.register_plugin(manifest("fake_test"))
    await enable("fake_test", events=["job.complete"])

    ok = await client.post("/api/v1/plugins/fake_test/test", json={})
    broken = await client.post("/api/v1/plugins/fake_test/test", json={"settings": {"mode": "raise"}})

    assert ok.json() == {"ok": True}
    assert broken.json()["ok"] is False and "upstream said no" in broken.json()["message"]


async def test_channel_settings_and_events_are_discoverable_from_the_plugin_detail(client):
    plugins.register_plugin(manifest("fake_ui"))
    body = (await client.get("/api/v1/plugins/fake_ui")).json()

    props = body["settings_schema"]["properties"]
    assert props["events"]["items"]["enum"][:2] == ["job.complete", "job.failed"]
    assert body["secret_fields"] == ["api_token"] and [p["capability"] for p in body["provides"]] == ["notify.channel"]
