"""Thin, mockable wrapper around aiosmtplib - same pattern as app/oracle/connector.py.
Tests inject a fake EmailSender instead of needing a real SMTP relay.
"""

from email.message import EmailMessage
from typing import Protocol

import aiosmtplib


class EmailSender(Protocol):
    async def __call__(
        self,
        *,
        host: str,
        port: int,
        use_tls: bool,
        username: str | None,
        password: str | None,
        sender: str,
        recipients: list[str],
        subject: str,
        body: str,
    ) -> None: ...


async def send_email(
    *,
    host: str,
    port: int,
    use_tls: bool,
    username: str | None,
    password: str | None,
    sender: str,
    recipients: list[str],
    subject: str,
    body: str,
) -> None:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)

    await aiosmtplib.send(
        message,
        hostname=host,
        port=port,
        username=username or None,
        password=password or None,
        start_tls=use_tls,
        timeout=10,
    )
