"""The bundled notification channel plugins (BIZ-252): what each sends, what it skips, and that secrets never leak."""
import httpx
import pytest

from app.plugins.capabilities.notify_channel import ChannelMessage
from app.plugins.notify_discord import channel as discord_mod
from app.plugins.notify_discord.channel import DiscordChannel
from app.plugins.notify_discord.settings import DiscordSettings
from app.plugins.notify_email import channel as email_mod
from app.plugins.notify_email.channel import EmailChannel
from app.plugins.notify_email.settings import EmailSettings
from app.plugins.notify_ntfy import channel as ntfy_mod
from app.plugins.notify_ntfy.channel import NtfyChannel
from app.plugins.notify_ntfy.settings import NtfySettings

_REAL_CLIENT = httpx.AsyncClient

MSG = ChannelMessage("job.complete", "Themis: job done", "benchy.3mf finished on Atlas", job_id=7)


def wire(monkeypatch, module, *, status=200, error: Exception | None = None):
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        if error is not None:
            raise error
        return httpx.Response(status)

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    return requests


def ntfy(**kw) -> NtfyChannel:
    return NtfyChannel(NtfySettings(server_url="https://ntfy.example.test/", topic="themis", events=["job.complete"], **kw))


# --- ntfy -------------------------------------------------------------------------------------------------------

async def test_ntfy_posts_the_topic_title_and_message_and_priority_when_set(monkeypatch):
    sent = wire(monkeypatch, ntfy_mod)
    result = await ntfy(priority=4).deliver(MSG)

    assert (result.ok, result.skipped) == (True, False)
    (request,) = sent
    assert str(request.url) == "https://ntfy.example.test" and request.method == "POST"
    import json
    assert json.loads(request.content) == {"topic": "themis", "title": "Themis: job done", "message": "benchy.3mf finished on Atlas", "priority": 4}


async def test_ntfy_omits_the_priority_when_unset(monkeypatch):
    sent = wire(monkeypatch, ntfy_mod)
    await ntfy().deliver(MSG)
    import json
    assert "priority" not in json.loads(sent[0].content)


async def test_a_channel_skips_events_outside_its_allow_list_and_an_empty_list_sends_nothing(monkeypatch):
    sent = wire(monkeypatch, ntfy_mod)
    other = await ntfy().deliver(ChannelMessage("job.failed", "t", "m"))
    nothing = await NtfyChannel(NtfySettings(server_url="https://n.test", topic="t", events=[])).deliver(MSG)

    assert (other.ok, other.skipped, nothing.ok, nothing.skipped) == (True, True, True, True) and sent == []


async def test_an_unconfigured_channel_skips_quietly(monkeypatch):
    sent = wire(monkeypatch, ntfy_mod)
    result = await NtfyChannel(NtfySettings(events=["job.complete"])).deliver(MSG)
    assert (result.ok, result.skipped) == (True, True) and sent == []


async def test_a_failing_ntfy_server_is_reported_not_raised(monkeypatch):
    wire(monkeypatch, ntfy_mod, status=500)
    result = await ntfy().deliver(MSG)
    assert (result.ok, result.error) == (False, "RuntimeError: ntfy server responded 500")


async def test_ntfy_test_connection_sends_a_test_message_and_raises_on_failure(monkeypatch):
    sent = wire(monkeypatch, ntfy_mod)
    assert await ntfy().test_connection() is None            # the test ignores the event allow-list
    import json
    assert json.loads(sent[0].content)["title"] == "Themis test notification"

    wire(monkeypatch, ntfy_mod, status=403)
    with pytest.raises(RuntimeError, match="responded 403"):
        await ntfy().test_connection()
    with pytest.raises(RuntimeError, match="not fully configured"):
        await NtfyChannel(NtfySettings()).test_connection()


# --- Discord ----------------------------------------------------------------------------------------------------

HOOK = "https://discord.example.test/api/webhooks/123/SECRET-TOKEN"


async def test_discord_posts_the_message_text_and_the_test_posts_title_and_message(monkeypatch):
    sent = wire(monkeypatch, discord_mod)
    ch = DiscordChannel(DiscordSettings(webhook_url=HOOK, events=["job.complete"]))
    await ch.deliver(MSG)
    await ch.test_connection()

    import json
    assert [json.loads(r.content) for r in sent] == [{"content": "benchy.3mf finished on Atlas"},
                                                      {"content": "Themis test notification\nThis is a test notification from Themis."}]
    assert all(str(r.url) == HOOK for r in sent)


async def test_discord_errors_never_contain_the_webhook_token(monkeypatch):
    wire(monkeypatch, discord_mod, error=httpx.ConnectError(f"cannot reach {HOOK}"))
    ch = DiscordChannel(DiscordSettings(webhook_url=HOOK, events=["job.complete"]))

    result = await ch.deliver(MSG)
    with pytest.raises(RuntimeError) as exc:
        await ch.test_connection()

    assert result.ok is False and "SECRET-TOKEN" not in result.error and "SECRET-TOKEN" not in str(exc.value)
    assert "***" in result.error


# --- email ------------------------------------------------------------------------------------------------------

def email(**kw) -> EmailChannel:
    base = dict(host="smtp.example.test", port=587, username="u", password="smtp-pass-123", from_addr="themis@example.test",
                to_addrs=["a@example.test", "b@example.test"], events=["job.complete"])
    return EmailChannel(EmailSettings(**{**base, **kw}))


async def test_email_sends_title_as_subject_and_message_as_body_to_every_recipient(monkeypatch):
    calls = []
    monkeypatch.setattr(email_mod, "_send_sync", lambda *a: calls.append(a))

    result = await email().deliver(MSG)

    assert (result.ok, result.skipped) == (True, False)
    assert calls == [("smtp.example.test", 587, "u", "smtp-pass-123", "themis@example.test", ["a@example.test", "b@example.test"],
                      "Themis: job done", "benchy.3mf finished on Atlas")]


@pytest.mark.parametrize("missing", ["host", "port", "from_addr", "to_addrs"])
async def test_email_needs_host_port_from_and_recipients(monkeypatch, missing):
    calls = []
    monkeypatch.setattr(email_mod, "_send_sync", lambda *a: calls.append(a))
    result = await email(**{missing: [] if missing == "to_addrs" else None}).deliver(MSG)
    assert result.skipped is True and calls == []


async def test_email_failures_do_not_leak_the_password(monkeypatch):
    def boom(*a):
        raise OSError("auth failed for password smtp-pass-123")

    monkeypatch.setattr(email_mod, "_send_sync", boom)

    result = await email().deliver(MSG)

    assert result.ok is False and "smtp-pass-123" not in result.error


# --- SMTP behaviour (parity with the removed sender) --------------------------------------------------------------

class FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout, self.calls = host, port, timeout, []
        self.refuse_starttls = False
        FakeSMTP.instances.append(self)

    def starttls(self):
        import smtplib
        self.calls.append("starttls")
        if self.refuse_starttls:
            raise smtplib.SMTPNotSupportedError("no STARTTLS")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.calls.append(("send", msg["Subject"], msg["To"], msg["From"], msg.get_payload()))

    def quit(self):
        self.calls.append("quit")


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(email_mod.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def test_smtp_uses_starttls_logs_in_only_with_both_credentials_and_always_quits(smtp):
    email_mod._send_sync("smtp.test", 587, "u", "p", "a@x.test", ["b@x.test", "c@x.test"], "Subject", "Body")
    email_mod._send_sync("smtp.test", 25, "u", None, "a@x.test", ["b@x.test"], "S", "B")        # username without password: no login

    first, second = smtp.instances
    assert (first.host, first.port, first.timeout) == ("smtp.test", 587, 10)
    assert first.calls == ["starttls", ("login", "u", "p"), ("send", "Subject", "b@x.test, c@x.test", "a@x.test", "Body"), "quit"]
    assert not any(isinstance(c, tuple) and c[0] == "login" for c in second.calls)


def test_a_server_without_starttls_is_still_used(monkeypatch, smtp):
    class NoTls(FakeSMTP):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.refuse_starttls = True

    monkeypatch.setattr(email_mod.smtplib, "SMTP", NoTls)
    email_mod._send_sync("smtp.test", 25, None, None, "a@x.test", ["b@x.test"], "S", "B")
    assert NoTls.instances[-1].calls == ["starttls", ("send", "S", "b@x.test", "a@x.test", "B"), "quit"]
