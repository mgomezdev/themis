"""Email notifications over SMTP (BIZ-252)."""
from __future__ import annotations

import asyncio
import smtplib
from email.mime.text import MIMEText

from ..capabilities.notify_channel import ChannelMessage, FilteredChannel
from .settings import EmailSettings


def _send_sync(host: str, port: int, username: str | None, password: str | None, from_addr: str, to_addrs: list[str],
               subject: str, body: str) -> None:
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


class EmailChannel(FilteredChannel):
    def __init__(self, settings: EmailSettings) -> None:
        self.settings = settings
        self.secrets = (settings.password or "",)

    def configured(self) -> bool:
        s = self.settings
        return bool(s.host and s.port and s.from_addr and s.to_addrs)

    async def send(self, message: ChannelMessage) -> None:
        s = self.settings
        await asyncio.to_thread(_send_sync, s.host or "", s.port or 0, s.username, s.password, s.from_addr or "", list(s.to_addrs),
                                message.title, message.message)
