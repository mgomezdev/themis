"""Tests for notification_service: send_ntfy, send_discord, send_email, dispatch.

Assumed behaviors verified here:
  1. send_ntfy POSTs JSON to the server's base URL (no /topic suffix), body has
     topic/title/message, and includes "priority" only when explicitly given.
  2. send_discord POSTs {"content": ...} to the webhook URL, nothing else.
  3. Both ntfy/discord swallow non-2xx responses and exceptions (fire-and-forget).
  4. send_email drives smtplib.SMTP synchronously via asyncio.to_thread: connects,
     attempts starttls(), logs in only when both username+password given, sends a
     MIMEText with Subject/From/To headers set, quits, and swallows exceptions.
  5. dispatch fans out to enabled channels whose per-channel events list contains
     the event — empty events list means "never fires", unlike webhook_config.
"""
import smtplib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.notification_service import (
    send_ntfy,
    send_discord,
    send_email,
    dispatch,
)


def _mock_client(post_response: httpx.Response = None, post_exception: Exception = None):
    """Return (patch_context, mock_client) that patches httpx.AsyncClient for POST."""
    mock_instance = AsyncMock()
    if post_exception is not None:
        mock_instance.post = AsyncMock(side_effect=post_exception)
    else:
        mock_instance.post = AsyncMock(return_value=post_response)

    @asynccontextmanager
    async def _ctx(*args, **kwargs):
        yield mock_instance

    return patch("app.services.notification_service.httpx.AsyncClient", side_effect=_ctx), mock_instance


def _ok_response() -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


def _error_response(status: int) -> httpx.Response:
    resp = httpx.Response(status, text="error")
    resp._request = httpx.Request("POST", "http://example.test")
    return resp


# ---------------------------------------------------------------------------
# send_ntfy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_ntfy_posts_to_server_base_url_no_topic_suffix():
    ctx, mock_client = _mock_client(_ok_response())
    with ctx:
        await send_ntfy("http://ntfy.test", "myprinter", "Job done", "Job 42 completed")

    mock_client.post.assert_called_once()
    call = mock_client.post.call_args
    url = call.args[0] if call.args else call.kwargs.get("url")
    assert url == "http://ntfy.test"


@pytest.mark.asyncio
async def test_send_ntfy_strips_trailing_slash_from_server_url():
    ctx, mock_client = _mock_client(_ok_response())
    with ctx:
        await send_ntfy("http://ntfy.test/", "myprinter", "Job done", "Job 42 completed")

    call = mock_client.post.call_args
    url = call.args[0] if call.args else call.kwargs.get("url")
    assert url == "http://ntfy.test"


@pytest.mark.asyncio
async def test_send_ntfy_body_without_priority():
    ctx, mock_client = _mock_client(_ok_response())
    with ctx:
        await send_ntfy("http://ntfy.test", "myprinter", "Job done", "Job 42 completed")

    body = mock_client.post.call_args.kwargs["json"]
    assert body == {"topic": "myprinter", "title": "Job done", "message": "Job 42 completed"}
    assert "priority" not in body


@pytest.mark.asyncio
async def test_send_ntfy_body_with_priority():
    ctx, mock_client = _mock_client(_ok_response())
    with ctx:
        await send_ntfy("http://ntfy.test", "myprinter", "Job done", "Job 42 completed", priority=5)

    body = mock_client.post.call_args.kwargs["json"]
    assert body == {
        "topic": "myprinter",
        "title": "Job done",
        "message": "Job 42 completed",
        "priority": 5,
    }


@pytest.mark.asyncio
async def test_send_ntfy_non_2xx_does_not_raise():
    ctx, _ = _mock_client(_error_response(500))
    with ctx:
        await send_ntfy("http://ntfy.test", "myprinter", "Job done", "Job 42 completed")
    # no exception propagated


@pytest.mark.asyncio
async def test_send_ntfy_exception_does_not_raise():
    ctx, _ = _mock_client(post_exception=httpx.ConnectError("boom"))
    with ctx:
        await send_ntfy("http://ntfy.test", "myprinter", "Job done", "Job 42 completed")
    # no exception propagated


# ---------------------------------------------------------------------------
# send_discord
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_discord_posts_to_webhook_url_with_content_only():
    ctx, mock_client = _mock_client(_ok_response())
    with ctx:
        await send_discord("http://discord.test/api/webhooks/1/abc", "Job 42 completed")

    mock_client.post.assert_called_once()
    call = mock_client.post.call_args
    url = call.args[0] if call.args else call.kwargs.get("url")
    assert url == "http://discord.test/api/webhooks/1/abc"
    body = call.kwargs["json"]
    assert body == {"content": "Job 42 completed"}


@pytest.mark.asyncio
async def test_send_discord_non_2xx_does_not_raise():
    ctx, _ = _mock_client(_error_response(400))
    with ctx:
        await send_discord("http://discord.test/api/webhooks/1/abc", "Job 42 completed")


@pytest.mark.asyncio
async def test_send_discord_exception_does_not_raise():
    ctx, _ = _mock_client(post_exception=httpx.ConnectError("boom"))
    with ctx:
        await send_discord("http://discord.test/api/webhooks/1/abc", "Job 42 completed")


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_email_connects_starttls_sends_and_quits():
    mock_smtp_instance = MagicMock()
    mock_smtp_cls = MagicMock(return_value=mock_smtp_instance)
    with patch("app.services.notification_service.smtplib.SMTP", mock_smtp_cls):
        await send_email(
            "smtp.test", 587, None, None,
            "from@example.com", ["to@example.com"],
            "Job done", "Job 42 completed",
        )

    mock_smtp_cls.assert_called_once_with("smtp.test", 587, timeout=10)
    mock_smtp_instance.starttls.assert_called_once()
    mock_smtp_instance.login.assert_not_called()
    mock_smtp_instance.send_message.assert_called_once()
    sent_msg = mock_smtp_instance.send_message.call_args.args[0]
    assert sent_msg["Subject"] == "Job done"
    assert sent_msg["From"] == "from@example.com"
    assert sent_msg["To"] == "to@example.com"
    mock_smtp_instance.quit.assert_called_once()


@pytest.mark.asyncio
async def test_send_email_multiple_recipients_joined_in_to_header():
    mock_smtp_instance = MagicMock()
    mock_smtp_cls = MagicMock(return_value=mock_smtp_instance)
    with patch("app.services.notification_service.smtplib.SMTP", mock_smtp_cls):
        await send_email(
            "smtp.test", 587, None, None,
            "from@example.com", ["a@example.com", "b@example.com"],
            "Job done", "Job 42 completed",
        )

    sent_msg = mock_smtp_instance.send_message.call_args.args[0]
    assert sent_msg["To"] == "a@example.com, b@example.com"


@pytest.mark.asyncio
async def test_send_email_logs_in_when_username_and_password_given():
    mock_smtp_instance = MagicMock()
    mock_smtp_cls = MagicMock(return_value=mock_smtp_instance)
    with patch("app.services.notification_service.smtplib.SMTP", mock_smtp_cls):
        await send_email(
            "smtp.test", 587, "user", "pass",
            "from@example.com", ["to@example.com"],
            "Job done", "Job 42 completed",
        )

    mock_smtp_instance.login.assert_called_once_with("user", "pass")


@pytest.mark.asyncio
async def test_send_email_does_not_log_in_when_only_username_given():
    mock_smtp_instance = MagicMock()
    mock_smtp_cls = MagicMock(return_value=mock_smtp_instance)
    with patch("app.services.notification_service.smtplib.SMTP", mock_smtp_cls):
        await send_email(
            "smtp.test", 587, "user", None,
            "from@example.com", ["to@example.com"],
            "Job done", "Job 42 completed",
        )

    mock_smtp_instance.login.assert_not_called()


@pytest.mark.asyncio
async def test_send_email_starttls_not_supported_continues_without_tls():
    mock_smtp_instance = MagicMock()
    mock_smtp_instance.starttls.side_effect = smtplib.SMTPNotSupportedError("no TLS")
    mock_smtp_cls = MagicMock(return_value=mock_smtp_instance)
    with patch("app.services.notification_service.smtplib.SMTP", mock_smtp_cls):
        await send_email(
            "smtp.test", 25, None, None,
            "from@example.com", ["to@example.com"],
            "Job done", "Job 42 completed",
        )

    mock_smtp_instance.send_message.assert_called_once()
    mock_smtp_instance.quit.assert_called_once()


@pytest.mark.asyncio
async def test_send_email_login_exception_does_not_raise():
    mock_smtp_instance = MagicMock()
    mock_smtp_instance.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")
    mock_smtp_cls = MagicMock(return_value=mock_smtp_instance)
    with patch("app.services.notification_service.smtplib.SMTP", mock_smtp_cls):
        await send_email(
            "smtp.test", 587, "user", "pass",
            "from@example.com", ["to@example.com"],
            "Job done", "Job 42 completed",
        )
    # no exception propagated


@pytest.mark.asyncio
async def test_send_email_connection_exception_does_not_raise():
    mock_smtp_cls = MagicMock(side_effect=OSError("connection refused"))
    with patch("app.services.notification_service.smtplib.SMTP", mock_smtp_cls):
        await send_email(
            "smtp.test", 587, None, None,
            "from@example.com", ["to@example.com"],
            "Job done", "Job 42 completed",
        )
    # no exception propagated


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

def _cfg(**overrides):
    base = dict(
        ntfy_enabled=False, ntfy_server_url="http://ntfy.test", ntfy_topic="topic", ntfy_events=[],
        discord_enabled=False, discord_webhook_url="http://discord.test/hook", discord_events=[],
        email_enabled=False, email_host="smtp.test", email_port=587, email_username=None,
        email_password=None, email_from_addr="from@example.com", email_to_addrs=["to@example.com"],
        email_events=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_dispatch_fires_only_matching_enabled_channel():
    cfg = _cfg(
        ntfy_enabled=True, ntfy_events=["job.complete"],
        discord_enabled=True, discord_events=["job.failed"],
    )
    with patch("app.services.notification_service.send_ntfy", AsyncMock()) as mock_ntfy, \
         patch("app.services.notification_service.send_discord", AsyncMock()) as mock_discord, \
         patch("app.services.notification_service.send_email", AsyncMock()) as mock_email:
        await dispatch(cfg, "job.complete", 42, "Job done", "Job 42 completed")

    mock_ntfy.assert_called_once()
    mock_discord.assert_not_called()
    mock_email.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_fires_discord_for_its_own_event():
    cfg = _cfg(
        ntfy_enabled=True, ntfy_events=["job.complete"],
        discord_enabled=True, discord_events=["job.failed"],
    )
    with patch("app.services.notification_service.send_ntfy", AsyncMock()) as mock_ntfy, \
         patch("app.services.notification_service.send_discord", AsyncMock()) as mock_discord:
        await dispatch(cfg, "job.failed", 42, "Job failed", "Job 42 failed")

    mock_discord.assert_called_once()
    mock_ntfy.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_disabled_channel_never_fires_even_if_event_matches():
    cfg = _cfg(ntfy_enabled=False, ntfy_events=["job.complete"])
    with patch("app.services.notification_service.send_ntfy", AsyncMock()) as mock_ntfy:
        await dispatch(cfg, "job.complete", 42, "Job done", "Job 42 completed")

    mock_ntfy.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_empty_events_list_never_fires_even_if_enabled():
    cfg = _cfg(ntfy_enabled=True, ntfy_events=[])
    with patch("app.services.notification_service.send_ntfy", AsyncMock()) as mock_ntfy:
        await dispatch(cfg, "job.complete", 42, "Job done", "Job 42 completed")

    mock_ntfy.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_email_fires_with_its_config_fields():
    cfg = _cfg(email_enabled=True, email_events=["job.blocked"])
    with patch("app.services.notification_service.send_email", AsyncMock()) as mock_email:
        await dispatch(cfg, "job.blocked", 7, "Job blocked", "Job 7 blocked")

    mock_email.assert_called_once()
    call_kwargs = mock_email.call_args
    args = call_kwargs.args
    assert args[0] == "smtp.test"
    assert args[1] == 587
    assert args[4] == "from@example.com"
    assert args[5] == ["to@example.com"]


@pytest.mark.asyncio
async def test_dispatch_no_channels_enabled_calls_nothing():
    cfg = _cfg()
    with patch("app.services.notification_service.send_ntfy", AsyncMock()) as mock_ntfy, \
         patch("app.services.notification_service.send_discord", AsyncMock()) as mock_discord, \
         patch("app.services.notification_service.send_email", AsyncMock()) as mock_email:
        await dispatch(cfg, "job.complete", 42, "Job done", "Job 42 completed")

    mock_ntfy.assert_not_called()
    mock_discord.assert_not_called()
    mock_email.assert_not_called()
