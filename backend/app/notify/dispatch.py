from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.config import settings
from app.db.models import Match, Posting, User
from app.notify.email_resend import ResendEmailProvider
from app.notify.sms.base import SmsProvider

_SEND_CONCURRENCY = asyncio.Semaphore(10)


def _has_sms_number(user: User) -> bool:
    return bool(user.phone and user.phone.startswith("+"))


@dataclass
class DigestItem:
    user: User
    matches: list[tuple[Match, Posting]]


@dataclass(frozen=True)
class SendOutcome:
    """Whether a single send actually succeeded — callers must check this
    before treating a match as delivered (see spec: Error handling
    principles — 'All sends logged with delivery status'). `skipped` covers
    opted-out users / no matches, which is neither a success nor a failure
    worth retrying."""

    success: bool
    provider: str
    error: str | None = None
    skipped: bool = False


@dataclass(frozen=True)
class DigestOutcome:
    user: User
    matches: list[tuple[Match, Posting]]
    sms: SendOutcome
    email: SendOutcome | None

    @property
    def delivered(self) -> bool:
        """A digest counts as delivered if *either* channel got through —
        an expired Resend API key shouldn't permanently block SMS
        delivery, and vice versa."""
        return self.sms.success or bool(self.email and self.email.success)


@dataclass(frozen=True)
class ChannelDigestOutcome:
    user: User
    matches: list[tuple[Match, Posting]]
    channel: str
    send: SendOutcome


def _format_sms_digest(items: list[tuple[Match, Posting]]) -> str:
    cap = settings.digest_max_sms_matches
    shown = items[:cap]
    lines = [f"{posting.company}: {posting.title} — {posting.url}" for _, posting in shown]
    text = "New matches:\n" + "\n".join(lines)
    remaining = len(items) - len(shown)
    if remaining > 0:
        text += f"\n+{remaining} more — reply LIST for all"
    return text


def _format_email_digest(items: list[tuple[Match, Posting]]) -> str:
    if not items:
        return (
            "No new job matches today.\n\n"
            "We are still scanning throughout the day and will keep your profile active."
        )
    lines = [f"- {posting.company}: {posting.title}\n  {posting.url}" for _, posting in items]
    return "New matches for you:\n\n" + "\n\n".join(lines)


async def send_digest(
    user: User,
    matches: list[tuple[Match, Posting]],
    sms_provider: SmsProvider,
    email_provider: ResendEmailProvider,
) -> DigestOutcome:
    """Send one digest (SMS + email) to a single user, respecting opt-out."""
    if user.opted_out or not matches:
        skipped = SendOutcome(success=False, provider=sms_provider.name, skipped=True)
        return DigestOutcome(user=user, matches=matches, sms=skipped, email=None)

    sms_result: SendOutcome | None = None
    email_result: SendOutcome | None = None

    async def _send_sms() -> None:
        nonlocal sms_result
        if not _has_sms_number(user):
            sms_result = SendOutcome(success=False, provider=sms_provider.name, skipped=True)
            return
        async with _SEND_CONCURRENCY:
            result = await sms_provider.send(user.phone, _format_sms_digest(matches))
        sms_result = SendOutcome(success=result.success, provider=result.provider, error=result.error)

    async def _send_email() -> None:
        nonlocal email_result
        if not user.email:
            email_result = SendOutcome(success=False, provider="resend", skipped=True)
            return
        async with _SEND_CONCURRENCY:
            success = await email_provider.send(user.email, "New job matches", _format_email_digest(matches))
        email_result = SendOutcome(success=success, provider="resend", error=None if success else "send_failed")

    await asyncio.gather(_send_sms(), _send_email())
    assert sms_result is not None  # noqa: S101 - always set by _send_sms
    return DigestOutcome(user=user, matches=matches, sms=sms_result, email=email_result)


async def send_instant(
    user: User,
    match: Match,
    posting: Posting,
    sms_provider: SmsProvider,
) -> SendOutcome:
    """Send a single high-priority match immediately, bypassing the digest
    queue (see spec: Notifications — hybrid delivery)."""
    if user.opted_out or not _has_sms_number(user):
        return SendOutcome(success=False, provider=sms_provider.name, skipped=True)
    body = f"High match: {posting.company}: {posting.title} — {posting.url}"
    if match.blurb:
        body += f"\n{match.blurb}"
    async with _SEND_CONCURRENCY:
        result = await sms_provider.send(user.phone, body)
    return SendOutcome(success=result.success, provider=result.provider, error=result.error)


async def send_instants(
    items: list[tuple[User, Match, Posting]],
    sms_provider: SmsProvider,
) -> list[tuple[User, Match, Posting, SendOutcome]]:
    outcomes = await asyncio.gather(
        *(send_instant(user, match, posting, sms_provider) for user, match, posting in items)
    )
    return [(user, match, posting, outcome) for (user, match, posting), outcome in zip(items, outcomes)]


async def send_digests(
    items: list[DigestItem],
    sms_provider: SmsProvider,
    email_provider: ResendEmailProvider,
) -> list[DigestOutcome]:
    """Fan out digests to many users concurrently, bounded by _SEND_CONCURRENCY
    (see spec: Notifications — Dispatch concurrency)."""
    return list(
        await asyncio.gather(
            *(
                send_digest(item.user, item.matches, sms_provider, email_provider)
                for item in items
            )
        )
    )


async def send_email_digest(
    user: User,
    matches: list[tuple[Match, Posting]],
    email_provider: ResendEmailProvider,
) -> ChannelDigestOutcome:
    if not user.email or not user.email_digest_enabled:
        send = SendOutcome(success=False, provider="resend", skipped=True)
    else:
        async with _SEND_CONCURRENCY:
            success = await email_provider.send(
                user.email,
                "Your daily job matches",
                _format_email_digest(matches),
            )
        send = SendOutcome(
            success=success,
            provider="resend",
            error=None if success else "send_failed",
        )
    return ChannelDigestOutcome(user=user, matches=matches, channel="email", send=send)


async def send_email_digests(
    items: list[DigestItem], email_provider: ResendEmailProvider
) -> list[ChannelDigestOutcome]:
    return list(
        await asyncio.gather(
            *(send_email_digest(item.user, item.matches, email_provider) for item in items)
        )
    )


async def send_sms_digest(
    user: User,
    matches: list[tuple[Match, Posting]],
    sms_provider: SmsProvider,
) -> ChannelDigestOutcome:
    if not matches or user.opted_out or not _has_sms_number(user):
        send = SendOutcome(success=False, provider=sms_provider.name, skipped=True)
    else:
        async with _SEND_CONCURRENCY:
            result = await sms_provider.send(user.phone, _format_sms_digest(matches))
        send = SendOutcome(success=result.success, provider=result.provider, error=result.error)
    return ChannelDigestOutcome(user=user, matches=matches, channel="sms", send=send)


async def send_sms_digests(
    items: list[DigestItem], sms_provider: SmsProvider
) -> list[ChannelDigestOutcome]:
    return list(
        await asyncio.gather(
            *(send_sms_digest(item.user, item.matches, sms_provider) for item in items)
        )
    )
