from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_RESEND_API_URL = "https://api.resend.com/emails"


class ResendEmailProvider:
    """Digest email via the Resend HTTP API (https://resend.com/docs/api-reference/emails/send-email).

    Plain `httpx` POST rather than the `resend` SDK — the SDK is a thin
    wrapper over this exact endpoint and pulling it in would be a dependency
    for one call site, when every other outbound integration in this repo
    (ATS sources, SignalWire) already talks to its provider via raw httpx.
    """

    name = "resend"

    def __init__(self, api_key: str | None = None, from_email: str | None = None) -> None:
        self.api_key = api_key or settings.resend_api_key
        self.from_email = from_email or settings.resend_from_email

    async def send(self, to: str, subject: str, body: str, html: str | None = None) -> bool:
        """`body` is always sent as the plain-text part (required — some
        clients/spam filters prefer or require it); `html` is optional and,
        when given, is sent alongside it as a real multipart email rather
        than replacing the text part (better deliverability, and a sane
        fallback for text-only clients)."""
        payload = {
            "from": self.from_email,
            "to": [to],
            "subject": subject,
            "text": body,
        }
        if html is not None:
            payload["html"] = html
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    _RESEND_API_URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                response.raise_for_status()
            return True
        except httpx.HTTPError:
            logger.warning("resend send to %s failed", to, exc_info=True)
            return False
