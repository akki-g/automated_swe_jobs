from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.config import settings
from app.db.models import Match as MatchRow
from app.db.models import Message as MessageRow
from app.db.models import User
from app.db.session import session_scope
from app.matching.rank import AnthropicMessagesClient
from app.notify.dispatch import (
    ChannelDigestOutcome,
    SendOutcome,
    send_email_digests,
    send_instants,
    send_sms_digests,
)
from app.notify.email_resend import ResendEmailProvider
from app.notify.sms.signalwire import SignalWireSmsProvider
from app.pipeline import (
    MatchCycleResult,
    backfill_completed_profiles,
    gather_pending_digests,
    gather_previously_sent_email_matches,
    mark_stale_postings,
    match_new_postings,
    run_sources,
    store_new_postings,
)
from app.sources.base import Source
from app.sources.registry import registry
from app.watchlist.service import sync_watchlist_sources

logger = logging.getLogger(__name__)


async def reliable_tier_sources(session) -> list[Source]:
    """Sources implementing check_for_changes() — usable by the fast lane:
    the curated companies.yaml list plus every actively-detected watchlist
    company. Always resolved through the registry so the *same* Source
    instances are reused across cycles — see app.sources.registry for why
    that matters."""
    watchlist_sources = await sync_watchlist_sources(session)
    return registry.reliable_tier_sources() + watchlist_sources


async def default_sources(session) -> list[Source]:
    """Everything the slow lane sweeps: reliable tier + watchlist (also used
    by the fast lane) plus best-effort aggregators (slow-lane only — see
    spec: Sources, aggregator tier)."""
    return await reliable_tier_sources(session) + registry.aggregator_sources()


async def _mark_channels_delivered(
    session, match_ids: list[int], channels: list[str]
) -> None:
    if not match_ids:
        return
    rows = (
        (await session.execute(select(MatchRow).where(MatchRow.id.in_(match_ids))))
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    for row in rows:
        row.notified_channels = sorted(set(row.notified_channels or []).union(channels))
        row.notified_at = row.notified_at or now


async def _record_instant_outcomes(
    outcomes: list[tuple[object, MatchRow, object, SendOutcome]],
) -> None:
    """Log a Message row per instant-send attempt and mark only the ones
    that actually succeeded as notified — a failed/ambiguous send must stay
    un-notified so it's picked up again, not silently lost (see spec: Error
    handling principles — 'All sends logged with delivery status')."""
    if not outcomes:
        return
    now = datetime.now(UTC)
    succeeded_ids: list[int] = []
    async with session_scope() as session:
        for user, match, posting, outcome in outcomes:
            if outcome.skipped:
                continue
            session.add(
                MessageRow(
                    user_id=user.id,
                    match_id=match.id,
                    direction="outbound",
                    channel="sms",
                    provider=outcome.provider,
                    body=f"instant: {posting.company}: {posting.title}"[:4000],
                    status="sent" if outcome.success else "failed",
                    created_at=now,
                )
            )
            if outcome.success:
                succeeded_ids.append(match.id)
            else:
                logger.warning(
                    "instant send failed for user_id=%s match_id=%s: %s",
                    user.id,
                    match.id,
                    outcome.error,
                )
        if succeeded_ids:
            await _mark_channels_delivered(session, succeeded_ids, ["sms"])
        await session.commit()


async def _record_channel_digest_outcomes(
    outcomes: list[ChannelDigestOutcome], *, email_digest_sent_on: date | None = None
) -> None:
    if not outcomes:
        return
    now = datetime.now(UTC)
    async with session_scope() as session:
        for outcome in outcomes:
            if outcome.send.skipped:
                continue
            session.add(
                MessageRow(
                    user_id=outcome.user.id,
                    match_id=None,
                    direction="outbound",
                    channel=outcome.channel,
                    provider=outcome.send.provider,
                    body=f"digest: {len(outcome.matches)} match(es)",
                    status="sent" if outcome.send.success else "failed",
                    created_at=now,
                )
            )
            if outcome.send.success:
                await _mark_channels_delivered(
                    session,
                    [match_row.id for match_row, _posting_row in outcome.matches],
                    [outcome.channel],
                )
                if outcome.channel == "email" and email_digest_sent_on is not None:
                    user = await session.get(User, outcome.user.id)
                    if user is not None:
                        user.last_email_digest_sent_on = email_digest_sent_on
            else:
                logger.warning(
                    "%s digest delivery failed for user_id=%s: %s",
                    outcome.channel,
                    outcome.user.id,
                    outcome.send.error,
                )
        await session.commit()


async def _process_new_postings(
    sources: list[Source], lane: str, sms_provider: SignalWireSmsProvider
) -> MatchCycleResult:
    """Shared by both lanes: fetch -> store -> match -> instant send. Digest
    matches are only ever written here, never sent — the separate digest
    cycle owns delivery (see spec: Scheduling, Data flow)."""
    rank_client = AnthropicMessagesClient()

    async with session_scope() as session:
        postings = await run_sources(sources)
        new_rows = await store_new_postings(session, postings)
        logger.info("%s lane: %d new postings", lane, len(new_rows))

        result = await match_new_postings(session, new_rows, rank_client, lane=lane)
        await session.commit()

    if result.instant:
        outcomes = await send_instants(result.instant, sms_provider)
        await _record_instant_outcomes(outcomes)

    return result


async def _check_for_changes(source: Source) -> bool:
    try:
        return await source.check_for_changes()
    except NotImplementedError:
        return False
    except Exception:  # noqa: BLE001 - one source's check must not skip the rest
        logger.warning(
            "fast lane: check_for_changes failed for %s", source.name, exc_info=True
        )
        return False


async def fast_lane_cycle() -> MatchCycleResult:
    """Poll reliable-tier sources' cheap change signal; on a hit, fetch just
    that source and push high-priority matches out immediately. Best-effort
    by design — see spec: Scheduling. Normal-priority survivors are left for
    the digest cycle, same as the slow lane."""
    sms_provider = SignalWireSmsProvider()

    async with session_scope() as session:
        sources = await reliable_tier_sources(session)

    # Every source's check_for_changes() is an independent network round-trip
    # (a HEAD/GET against that source's own URL) with no shared state, so
    # checking them concurrently is what makes this lane worth calling
    # "fast" — checking N sources one-at-a-time would make the fast lane's
    # own polling latency scale with the size of companies.yaml.
    changed_flags = await asyncio.gather(
        *(_check_for_changes(source) for source in sources)
    )
    changed = [
        source for source, has_changed in zip(sources, changed_flags) if has_changed
    ]

    if not changed:
        return MatchCycleResult(instant=[], digest_items=[])

    return await _process_new_postings(changed, lane="fast", sms_provider=sms_provider)


async def slow_lane_cycle() -> MatchCycleResult:
    """The correctness backstop: full sweep across every source tier,
    staleness marking, and re-matching (idempotent against whatever the fast
    lane already wrote — see spec: Data flow)."""
    sms_provider = SignalWireSmsProvider()

    async with session_scope() as session:
        stale_count = await mark_stale_postings(session)
        if stale_count:
            logger.info("slow lane: marked %d postings stale", stale_count)
        await session.commit()
        sources = await default_sources(session)

    return await _process_new_postings(sources, lane="slow", sms_provider=sms_provider)


async def profile_backfill_cycle() -> MatchCycleResult:
    """Give newly-completed profiles matches from jobs already in storage.

    This is independent of scrape cadence: a new user should not have to wait
    for a company to publish another job before anything can appear on their
    dashboard. The pipeline bounds users and postings per run and records a
    completion marker, making this safe to poll frequently.
    """
    rank_client = AnthropicMessagesClient()
    sms_provider = SignalWireSmsProvider()
    async with session_scope() as session:
        result = await backfill_completed_profiles(session, rank_client)
        await session.commit()

    if result.instant:
        outcomes = await send_instants(result.instant, sms_provider)
        await _record_instant_outcomes(outcomes)
    return result


async def digest_cycle() -> list[ChannelDigestOutcome]:
    """Legacy 08:00/20:00 SMS digest, retained for SMS-opted-in users."""
    sms_provider = SignalWireSmsProvider()

    async with session_scope() as session:
        digest_items = await gather_pending_digests(session, channel="sms")

    if not digest_items:
        return []

    outcomes = await send_sms_digests(digest_items, sms_provider)
    await _record_channel_digest_outcomes(outcomes)
    return outcomes


async def daily_email_cycle(now: datetime | None = None) -> list[ChannelDigestOutcome]:
    """Send each completed web profile one email split into "Just Dropped"
    (matches never emailed before) and "For You" (matches an earlier email
    already showed, still relevant) — see spec addendum: email digest
    sections. Matching cadence stays owned by the fast/slow lanes."""
    email_provider = ResendEmailProvider()
    local_now = now or datetime.now(ZoneInfo(settings.scheduler_timezone))
    due_time = local_now.strftime("%H:%M")
    digest_date = local_now.date()
    async with session_scope() as session:
        digest_items = await gather_pending_digests(
            session,
            channel="email",
            email_due_time=due_time,
            email_digest_date=digest_date,
        )
        already_sent_by_user = await gather_previously_sent_email_matches(
            session, [item.user.id for item in digest_items]
        )
    if not digest_items:
        return []
    outcomes = await send_email_digests(
        digest_items, already_sent_by_user, email_provider
    )
    await _record_channel_digest_outcomes(outcomes, email_digest_sent_on=digest_date)
    return outcomes


def build_scheduler() -> AsyncIOScheduler:
    # APScheduler's default misfire grace is only one second. A broad source
    # response can briefly keep the event loop busy parsing/deduplicating
    # thousands of jobs; production skipped a profile backfill merely because
    # it started 1.745 seconds late. Coalesce delayed ticks and allow five
    # minutes so useful work runs once after the process catches up.
    scheduler = AsyncIOScheduler(
        timezone=settings.scheduler_timezone,
        job_defaults={"coalesce": True, "misfire_grace_time": 300},
    )
    scheduler.add_job(
        fast_lane_cycle,
        "interval",
        minutes=settings.fast_lane_interval_minutes,
        id="fast_lane_cycle",
        max_instances=1,
    )
    scheduler.add_job(
        slow_lane_cycle,
        "interval",
        minutes=settings.slow_lane_interval_minutes,
        # Refresh persisted inventory immediately after a restart. Otherwise
        # the first scrape waits 15 minutes while the one-minute profile
        # backfill sees an empty/stale database. The backfill itself now
        # retries safely, but eager refresh shortens the empty-dashboard gap.
        next_run_time=datetime.now(UTC),
        id="slow_lane_cycle",
        max_instances=1,
    )
    scheduler.add_job(
        profile_backfill_cycle,
        "interval",
        minutes=settings.profile_backfill_interval_minutes,
        id="profile_backfill_cycle",
        max_instances=1,
    )
    scheduler.add_job(
        digest_cycle,
        CronTrigger(hour=settings.digest_hours, timezone=settings.scheduler_timezone),
        id="digest_cycle",
        max_instances=1,
    )
    scheduler.add_job(
        daily_email_cycle,
        CronTrigger(minute="*", timezone=settings.scheduler_timezone),
        id="daily_email_cycle",
        max_instances=1,
    )
    return scheduler
