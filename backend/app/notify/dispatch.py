from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.config import settings
from app.db.models import Match, Posting, User
from app.notify.curate import curate_matches
from app.notify.email_resend import ResendEmailProvider
from app.notify.email_template import render_digest_email
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


def _union_by_match(
    *groups: list[tuple[Match, Posting]],
) -> list[tuple[Match, Posting]]:
    """Every match named by at least one channel, de-duplicated on the Match
    object itself (id is None until flush, so identity is the safe key)."""
    seen: set[int] = set()
    merged: list[tuple[Match, Posting]] = []
    for group in groups:
        for match, posting in group:
            if id(match) in seen:
                continue
            seen.add(id(match))
            merged.append((match, posting))
    return merged


def _matches_url() -> str:
    """Deep link back to the web app's matches view — App.tsx reads
    ?view=matches on load and opens MatchesPage directly (no client-side
    router to otherwise intercept a path like /matches — see
    frontend/src/App.tsx)."""
    return f"{settings.frontend_app_url.rstrip('/')}/?view=matches"


def _format_sms_digest(items: list[tuple[Match, Posting]]) -> tuple[str, list[tuple[Match, Posting]]]:
    """Returns (text, sent) where `sent` is the curated subset the message
    actually names. Callers must record `sent` — not `items` — as delivered:
    anything curated out was never shown to the user and has to stay pending
    for the next digest (see send_sms_digest)."""
    shown = curate_matches(
        items,
        overall_cap=settings.digest_max_sms_matches,
        per_company_cap=settings.digest_max_per_company,
    )
    lines = [f"{posting.company}: {posting.title} — {posting.url}" for _, posting in shown]
    text = "New matches:\n" + "\n".join(lines)
    remaining = len(items) - len(shown)
    if remaining > 0:
        text += f"\n+{remaining} more — reply LIST for all"
    return text, shown


def _format_email_digest(
    user: User, items: list[tuple[Match, Posting]]
) -> tuple[str, str, list[tuple[Match, Posting]]]:
    """Returns (text, html, sent) — curated to a relevant, diverse top-N
    rather than every pending match (see spec addendum: digest curation — an
    uncapped, single-company-dominated digest is what actually made early
    digests feel irrelevant/spammy).

    `sent` is returned for the same reason as in _format_sms_digest: the
    caller must mark only what the email actually listed as notified.
    Marking the full `items` would let curation silently consume matches the
    user never saw — with a real 63-match backlog that permanently swallowed
    48 of them, since a notified match is never picked up by a later digest.
    """
    curated = curate_matches(
        items,
        overall_cap=settings.digest_max_email_matches,
        per_company_cap=settings.digest_max_per_company,
    )
    text, html = render_digest_email(user.name, curated, _matches_url())
    return text, html, curated


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
    # What each channel actually named. The two channels curate to different
    # caps, so neither list is guaranteed to contain the other — the outcome
    # carries their union, since _record_digest_outcomes marks matches once
    # either channel delivers.
    sms_sent: list[tuple[Match, Posting]] = []
    email_sent: list[tuple[Match, Posting]] = []

    async def _send_sms() -> None:
        nonlocal sms_result, sms_sent
        if not _has_sms_number(user):
            sms_result = SendOutcome(success=False, provider=sms_provider.name, skipped=True)
            return
        body, sms_sent = _format_sms_digest(matches)
        async with _SEND_CONCURRENCY:
            result = await sms_provider.send(user.phone, body)
        sms_result = SendOutcome(success=result.success, provider=result.provider, error=result.error)

    async def _send_email() -> None:
        nonlocal email_result, email_sent
        if not user.email:
            email_result = SendOutcome(success=False, provider="resend", skipped=True)
            return
        text, html, email_sent = _format_email_digest(user, matches)
        async with _SEND_CONCURRENCY:
            success = await email_provider.send(user.email, "New job matches", text, html=html)
        email_result = SendOutcome(success=success, provider="resend", error=None if success else "send_failed")

    await asyncio.gather(_send_sms(), _send_email())
    assert sms_result is not None  # noqa: S101 - always set by _send_sms
    return DigestOutcome(
        user=user,
        matches=_union_by_match(sms_sent, email_sent),
        sms=sms_result,
        email=email_result,
    )


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
    sent = matches
    if not user.email or not user.email_digest_enabled:
        send = SendOutcome(success=False, provider="resend", skipped=True)
    else:
        text, html, sent = _format_email_digest(user, matches)
        async with _SEND_CONCURRENCY:
            success = await email_provider.send(
                user.email,
                "Your daily job matches",
                text,
                html=html,
            )
        send = SendOutcome(
            success=success,
            provider="resend",
            error=None if success else "send_failed",
        )
    # `sent`, not `matches`: only what the email actually listed may be
    # marked notified, or curation silently eats the rest (see
    # _format_email_digest). Anything held back stays pending and leads the
    # next digest, since curate_matches orders by score.
    return ChannelDigestOutcome(user=user, matches=sent, channel="email", send=send)


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
    sent = matches
    if not matches or user.opted_out or not _has_sms_number(user):
        send = SendOutcome(success=False, provider=sms_provider.name, skipped=True)
    else:
        body, sent = _format_sms_digest(matches)
        async with _SEND_CONCURRENCY:
            result = await sms_provider.send(user.phone, body)
        send = SendOutcome(success=result.success, provider=result.provider, error=result.error)
    # Only the matches the SMS actually named — the "+N more" tail stays
    # pending so a later digest can carry it, rather than being consumed by
    # a message that never mentioned it.
    return ChannelDigestOutcome(user=user, matches=sent, channel="sms", send=send)


async def send_sms_digests(
    items: list[DigestItem], sms_provider: SmsProvider
) -> list[ChannelDigestOutcome]:
    return list(
        await asyncio.gather(
            *(send_sms_digest(item.user, item.matches, sms_provider) for item in items)
        )
    )
