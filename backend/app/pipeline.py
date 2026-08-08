from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models import Criteria as CriteriaRow
from app.db.models import Match as MatchRow
from app.db.models import Posting as PostingRow
from app.db.models import User
from app.domain.models import Criteria, Posting, Priority, RoleType
from app.ingest.dedupe import dedupe_postings, filter_new
from app.ingest.normalize import normalize, normalize_company_key
from app.matching.filters import filter_postings
from app.matching.rank import AnthropicClient, compute_priority, rank_postings
from app.notify.dispatch import DigestItem
from app.sources.base import Source
from app.watchlist.service import watchlisted_company_keys

logger = logging.getLogger(__name__)


@dataclass
class MatchCycleResult:
    instant: list[tuple[User, MatchRow, PostingRow]]
    digest_items: list[DigestItem]


async def run_sources(sources: list[Source]) -> list[Posting]:
    """Fetch every source concurrently; a failing source contributes nothing
    (see spec: Source isolation) rather than aborting the cycle."""
    raw_batches = []
    for source in sources:
        try:
            raw_batches.append(await source.fetch())
        except Exception:  # noqa: BLE001 - isolate one bad source from the rest
            logger.warning("source %s failed", getattr(source, "name", source), exc_info=True)
            raw_batches.append([])

    normalized = [normalize(raw) for batch in raw_batches for raw in batch]
    return dedupe_postings(normalized)


def _new_posting_row(posting: Posting, now: datetime) -> PostingRow:
    return PostingRow(
        posting_key=posting.posting_key,
        source=posting.source,
        company=posting.company,
        title=posting.title,
        url=posting.url,
        location=posting.location,
        role_type=posting.role_type.value if posting.role_type else None,
        posted_at=posting.posted_at,
        raw=posting.raw,
        status="open",
        first_seen_at=now,
        last_seen_at=now,
    )


async def _insert_and_touch(
    session: AsyncSession, postings: list[Posting], now: datetime
) -> list[PostingRow]:
    """Bump last_seen_at on already-known postings and insert rows for new
    ones. Shared by the first attempt and the post-rollback retry so the
    last_seen_at bump never gets silently dropped on a retry (see
    store_new_postings)."""
    existing = (await session.execute(select(PostingRow))).scalars().all()
    existing_by_key = {row.posting_key: row for row in existing}

    for posting in postings:
        row = existing_by_key.get(posting.posting_key)
        if row is not None:
            row.last_seen_at = now

    new_postings = filter_new(postings, set(existing_by_key.keys()))
    new_rows: list[PostingRow] = []
    for posting in new_postings:
        row = _new_posting_row(posting, now)
        session.add(row)
        new_rows.append(row)
    return new_rows


async def store_new_postings(session: AsyncSession, postings: list[Posting]) -> list[PostingRow]:
    """Insert postings not already present (by posting_key); update
    last_seen_at on ones already known. Returns the newly-inserted rows."""
    now = datetime.now(UTC)
    new_rows = await _insert_and_touch(session, postings, now)

    try:
        await session.flush()
    except IntegrityError:
        # Another lane (or a concurrent cycle) inserted one of these
        # posting_keys first — see spec: Scheduling, idempotency across
        # lanes. Roll back this batch and redo the *whole* pass (including
        # the last_seen_at bumps, not just the new-row insert) against
        # what's actually in the DB now, rather than crash the cycle or
        # silently lose this cycle's freshness bumps.
        await session.rollback()
        new_rows = await _insert_and_touch(session, postings, now)
        await session.flush()

    return new_rows


async def mark_stale_postings(session: AsyncSession) -> int:
    """Postings not re-seen for N consecutive cycles are inferred closed (see
    spec: Sources — Posting staleness). Uses a time-based proxy (N cycles ×
    the slow-lane interval) rather than an explicit per-cycle counter, since
    the cadence is fixed."""
    threshold = datetime.now(UTC) - timedelta(
        minutes=settings.slow_lane_interval_minutes * settings.stale_after_cycles
    )
    result = await session.execute(
        update(PostingRow)
        .where(PostingRow.status == "open", PostingRow.last_seen_at < threshold)
        .values(status="stale")
    )
    return result.rowcount or 0


def _criteria_row_to_domain(row: CriteriaRow) -> Criteria:
    return Criteria(
        user_id=row.user_id,
        role_types=tuple(RoleType(rt) for rt in row.role_types),
        keywords=tuple(row.keywords),
        locations=tuple(row.locations),
        sponsorship_required=row.sponsorship_required,
        min_date=row.min_date,
        freeform_notes=row.freeform_notes,
    )


def _posting_row_to_domain(row: PostingRow) -> Posting:
    return Posting(
        posting_key=row.posting_key,
        source=row.source,
        company=row.company,
        title=row.title,
        url=row.url,
        location=row.location,
        role_type=RoleType(row.role_type) if row.role_type else None,
        posted_at=row.posted_at,
        raw=row.raw,
    )


async def match_new_postings(
    session: AsyncSession,
    new_postings: list[PostingRow],
    rank_client: AnthropicClient,
    lane: str = "slow",
) -> MatchCycleResult:
    """Rule-filter every new posting against every active user's criteria,
    then batch survivors to Claude for scoring/blurb (see spec: Matching).
    Splits results into instant (high-priority) sends and digest items."""
    if not new_postings:
        return MatchCycleResult(instant=[], digest_items=[])

    users = (
        (
            await session.execute(
                select(User)
                .where(User.opted_out.is_(False))
                .options(selectinload(User.criteria))
            )
        )
        .scalars()
        .all()
    )
    postings_domain = [_posting_row_to_domain(row) for row in new_postings]
    postings_by_key = {row.posting_key: row for row in new_postings}
    now = datetime.now(UTC)

    instant: list[tuple[User, MatchRow, PostingRow]] = []
    digest_items: list[DigestItem] = []

    for user in users:
        try:
            criteria_row = user.criteria
            if criteria_row is None:
                continue
            criteria = _criteria_row_to_domain(criteria_row)
            survivors = filter_postings(postings_domain, criteria)
            if not survivors:
                continue

            watched_keys = await watchlisted_company_keys(session, user.id)

            rank_results = await rank_postings(survivors, criteria, rank_client)
            rank_by_key = {r.posting_key: r for r in rank_results}

            matches_for_digest: list[tuple[MatchRow, PostingRow]] = []
            for posting in survivors:
                rank_result = rank_by_key.get(posting.posting_key)
                if rank_result is None:
                    # No score for this posting this cycle — either the LLM
                    # call failed (rank_postings already logged it) or the
                    # model just didn't return a result for this key. Leave
                    # it unmatched rather than writing a permanent
                    # score=0/blurb="" placeholder: the unique
                    # (user_id, posting_id) constraint on `matches` would
                    # otherwise block ever retrying it (see spec: Data
                    # flow — "A failed LLM call ... remain unmatched ... and
                    # are naturally re-evaluated next cycle").
                    continue

                posting_row = postings_by_key[posting.posting_key]
                priority = compute_priority(rank_result.score)
                is_watched = normalize_company_key(posting.company) in watched_keys
                if is_watched:
                    # An explicit watchlist add is a stronger signal than the
                    # score threshold — see spec addendum: watchlist priority.
                    priority = Priority.HIGH

                match_row = MatchRow(
                    user_id=user.id,
                    posting_id=posting_row.id,
                    score=rank_result.score,
                    blurb=rank_result.blurb,
                    priority=priority.value,
                    lane=lane,
                    match_reason="watchlist" if is_watched else "new_posting",
                    notified_channels=[],
                    notified_at=None,
                    created_at=now,
                )
                session.add(match_row)

                if priority == Priority.HIGH:
                    instant.append((user, match_row, posting_row))
                else:
                    matches_for_digest.append((match_row, posting_row))

            if matches_for_digest:
                digest_items.append(DigestItem(user=user, matches=matches_for_digest))
        except Exception:  # noqa: BLE001 - one user's bad state must not sink the whole cycle
            logger.warning("match_new_postings: failed for user_id=%s", user.id, exc_info=True)
            continue

    await session.flush()
    return MatchCycleResult(instant=instant, digest_items=digest_items)


async def gather_pending_digests(session: AsyncSession) -> list[DigestItem]:
    """Collect every un-sent normal-priority match, from either lane, grouped
    by user — the digest cycle's own read, decoupled from whatever cadence
    produced the matches (see spec: Data flow — Digest cycle)."""
    rows = (
        (
            await session.execute(
                select(MatchRow, PostingRow, User)
                .join(PostingRow, MatchRow.posting_id == PostingRow.id)
                .join(User, MatchRow.user_id == User.id)
                .where(
                    MatchRow.priority == Priority.NORMAL.value,
                    MatchRow.notified_at.is_(None),
                    User.opted_out.is_(False),
                )
            )
        )
        .all()
    )

    by_user: dict[int, DigestItem] = {}
    for match_row, posting_row, user in rows:
        item = by_user.get(user.id)
        if item is None:
            item = DigestItem(user=user, matches=[])
            by_user[user.id] = item
        item.matches.append((match_row, posting_row))

    return list(by_user.values())
