from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

import anyio

from app.config import settings

logger = logging.getLogger(__name__)


class GmailEmailProvider:
    """Digest email via Gmail SMTP with an app password. Good enough at
    'a few friends' scale; revisit if Phase 2 needs deliverability tracking
    beyond what Gmail's own sent-mail folder gives you."""

    name = "gmail"

    def __init__(self, address: str | None = None, app_password: str | None = None) -> None:
        self.address = address or settings.gmail_address
        self.app_password = app_password or settings.gmail_app_password

    async def send(self, to: str, subject: str, body: str) -> bool:
        return await anyio.to_thread.run_sync(self._send_sync, to, subject, body)

    def _send_sync(self, to: str, subject: str, body: str) -> bool:
        message = MIMEText(body)
        message["Subject"] = subject
        message["From"] = self.address
        message["To"] = to
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
                server.login(self.address, self.app_password)
                server.sendmail(self.address, [to], message.as_string())
            return True
        except smtplib.SMTPException:
            logger.warning("gmail send to %s failed", to, exc_info=True)
            return False
