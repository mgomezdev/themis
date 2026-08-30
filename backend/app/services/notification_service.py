"""Fire-and-forget built-in notification channels (ntfy, Discord, email) for
job complete/failed/blocked events. Additive alongside the generic webhook
system in webhook_service.py — this module doesn't touch that one."""
from __future__ import annotations
import asyncio
import logging
import smtplib
from email.mime.text import MIMEText

import httpx

from ..models import NotificationConfig

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0


async def send_ntfy(
    server_url: str,
    topic: str,
    title: str,
    message: str,
    priority: int | None = None,
) -> None:
    url = server_url.rstrip("/")
    body = {"topic": topic, "title": title, "message": message}
    if priority is not None:
        body["priority"] = priority
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=body)
        if not resp.is_success:
            logger.warning("ntfy POST %s → %s", url, resp.status_code)
    except Exception as exc:
        logger.warning("ntfy delivery failed for %s: %s", url, exc)


async def send_discord(webhook_url: str, content: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(webhook_url, json={"content": content})
        if not resp.is_success:
            logger.warning("Discord POST %s → %s", webhook_url, resp.status_code)
    except Exception as exc:
        logger.warning("Discord delivery failed for %s: %s", webhook_url, exc)


def _send_email_sync(
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    from_addr: str,
    to_addrs: list[str],
    subject: str,
    body: str,
) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)

    smtp = smtplib.SMTP(host, port, timeout=10)
    try:
        smtp.starttls()
    except smtplib.SMTPNotSupportedError:
        pass
    if username and password:
        smtp.login(username, password)
    smtp.send_message(msg)
    smtp.quit()


async def send_email(
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    from_addr: str,
    to_addrs: list[str],
    subject: str,
    body: str,
) -> None:
    try:
        await asyncio.to_thread(
            _send_email_sync, host, port, username, password, from_addr, to_addrs, subject, body
        )
    except Exception as exc:
        logger.warning("Email delivery failed for %s: %s", to_addrs, exc)


async def dispatch(
    cfg: "NotificationConfig",
    event: str,
    job_id: int,
    title: str,
    message: str,
) -> None:
    """Fan out a job event to every enabled channel whose per-channel events
    list contains it. Unlike webhook_config, an empty events list means the
    channel fires for nothing — it's explicit per-channel opt-in."""
    tasks = []

    if cfg.ntfy_enabled and event in cfg.ntfy_events:
        tasks.append(send_ntfy(cfg.ntfy_server_url, cfg.ntfy_topic, title, message))

    if cfg.discord_enabled and event in cfg.discord_events:
        tasks.append(send_discord(cfg.discord_webhook_url, message))

    if cfg.email_enabled and event in cfg.email_events:
        tasks.append(send_email(
            cfg.email_host, cfg.email_port, cfg.email_username, cfg.email_password,
            cfg.email_from_addr, cfg.email_to_addrs, title, message,
        ))

    if not tasks:
        return

    # Each send_* already swallows its own exceptions (fire-and-forget contract),
    # so a plain gather is enough — no channel's failure can cancel the others.
    await asyncio.gather(*tasks)
