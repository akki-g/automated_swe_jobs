from __future__ import annotations

import base64
import hashlib
import hmac
import logging

import httpx

from app.config import settings
from app.notify.sms.base import SendResult, SmsProvider

logger = logging.getLogger(__name__)


def compute_signature(url: str, params: dict[str, str], auth_token: str) -> str:
    """Twilio-compatible webhook signature (SignalWire's LaML API uses the
    same scheme): HMAC-SHA1 of the URL plus each sorted param's key+value,
    base64-encoded."""
    data = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify_signature(url: str, params: dict[str, str], signature: str, auth_token: str) -> bool:
    expected = compute_signature(url, params, auth_token)
    return hmac.compare_digest(expected, signature)


class SignalWireSmsProvider(SmsProvider):
    """SMS via SignalWire's LaML-compatible REST API."""

    name = "signalwire"

    def __init__(
        self,
        space: str | None = None,
        project_id: str | None = None,
        api_token: str | None = None,
        from_number: str | None = None,
    ) -> None:
        self.space = space or settings.signalwire_space
        self.project_id = project_id or settings.signalwire_project_id
        self.api_token = api_token or settings.signalwire_api_token
        self.from_number = from_number or settings.signalwire_from_number

    async def send(self, to: str, body: str) -> SendResult:
        url = (
            f"https://{self.space}.signalwire.com/api/laml/2010-04-01/"
            f"Accounts/{self.project_id}/Messages.json"
        )
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    url,
                    auth=(self.project_id, self.api_token),
                    data={"From": self.from_number, "To": to, "Body": body},
                )
        except httpx.TimeoutException:
            logger.warning("signalwire send to %s timed out", to)
            return SendResult(success=False, provider=self.name, ambiguous=True, error="timeout")
        except httpx.HTTPError as exc:
            logger.warning("signalwire send to %s failed: %s", to, exc)
            return SendResult(success=False, provider=self.name, ambiguous=True, error=str(exc))

        if response.status_code in (200, 201):
            return SendResult(success=True, provider=self.name)

        # An explicit rejection response is unambiguous — safe to fail over.
        return SendResult(
            success=False,
            provider=self.name,
            ambiguous=False,
            error=f"http_{response.status_code}: {response.text[:200]}",
        )
